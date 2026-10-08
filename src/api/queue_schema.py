"""Persistent queue schema and business-table synchronization (startup only)."""
from __future__ import annotations

import json

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_tasks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('upload','product','sku')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
    payload_json TEXT NOT NULL,
    dependency_id TEXT REFERENCES queue_tasks(task_id),
    result_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_queue_status_seq ON queue_tasks(status, seq);
CREATE INDEX IF NOT EXISTS idx_queue_dependency ON queue_tasks(dependency_id);
CREATE TABLE IF NOT EXISTS queue_idempotency (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES queue_tasks(task_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS queue_scheduler (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    state TEXT NOT NULL CHECK(state IN ('normal','cooldown','paused')),
    reason TEXT,
    until REAL,
    updated_at REAL NOT NULL
);
INSERT OR IGNORE INTO queue_scheduler VALUES (1, 'normal', NULL, NULL, 0);
CREATE TRIGGER IF NOT EXISTS queue_terminal_immutable
BEFORE UPDATE ON queue_tasks
WHEN OLD.status IN ('succeeded','failed','cancelled')
BEGIN SELECT RAISE(IGNORE); END;
"""


def encode(value: object) -> str:
    """Canonical JSON for exact, persistent idempotency comparisons."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


async def initialize_queue(db: aiosqlite.Connection, now: float) -> None:
    await db.executescript(SCHEMA)
    # Install before recovery so every legacy update is mirrored atomically.
    for table, kind in (("upload_tasks", "upload"), ("product_tasks", "product")):
        result = (
            "CASE WHEN NEW.image_id IS NOT NULL THEN json_object('image_id', NEW.image_id, "
            "'search_page_url', NEW.search_page_url) END"
            if kind == "upload" else "NEW.result_json"
        )
        await db.executescript(f"""
        CREATE TRIGGER IF NOT EXISTS {table}_terminal_immutable
        BEFORE UPDATE ON {table}
        WHEN OLD.status IN ('succeeded','failed','cancelled')
        BEGIN SELECT RAISE(IGNORE); END;
        CREATE TRIGGER IF NOT EXISTS {table}_queue_sync
        AFTER UPDATE ON {table}
        BEGIN
            UPDATE queue_tasks SET status=NEW.status, cancel_requested=NEW.cancel_requested,
                result_json={result}, error_code=NEW.error_code, error_message=NEW.error_message,
                updated_at=NEW.updated_at, started_at=NEW.started_at, finished_at=NEW.finished_at
            WHERE task_id=NEW.task_id;
        END;
        """)
    await db.execute("BEGIN IMMEDIATE")
    # Migrate completed parents too: identifiers, results and dependencies remain queryable.
    cursor = await db.execute("""
        SELECT task_id, 'upload' AS kind, created_at FROM upload_tasks
        UNION ALL SELECT task_id, 'product', created_at FROM product_tasks
        ORDER BY created_at, kind DESC, task_id
    """)
    # Legacy product timestamps normally follow their parent. Defer FK checks for ties/imports.
    await db.execute("PRAGMA defer_foreign_keys=ON")
    for task_id, kind, _ in await cursor.fetchall():
        table = "upload_tasks" if kind == "upload" else "product_tasks"
        c = await db.execute(f"SELECT * FROM {table} WHERE task_id=?", (task_id,))
        row = await c.fetchone()
        payload = {"image_url": row["image_url"]} if kind == "upload" else {"upload_task_id": row["upload_task_id"]}
        result = (encode({"image_id": row["image_id"], "search_page_url": row["search_page_url"]})
                  if kind == "upload" and row["image_id"] is not None else
                  row["result_json"] if kind == "product" else None)
        # NOT EXISTS avoids consuming AUTOINCREMENT values on subsequent startups.
        await db.execute("""
            INSERT INTO queue_tasks(task_id,kind,status,payload_json,dependency_id,result_json,
                cancel_requested,error_code,error_message,created_at,updated_at,started_at,finished_at)
            SELECT ?,?,?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS
                (SELECT 1 FROM queue_tasks WHERE task_id=?)
        """, (task_id, kind, row["status"], encode(payload),
              row["upload_task_id"] if kind == "product" else None, result,
              row["cancel_requested"], row["error_code"], row["error_message"],
              row["created_at"], row["updated_at"], row["started_at"], row["finished_at"], task_id))
    for table in ("upload_tasks", "product_tasks", "queue_tasks"):
        await db.execute(f"""
            UPDATE {table} SET status='cancelled', error_code='TASK_CANCELLED',
                error_message='任务已取消', finished_at=?, updated_at=?
            WHERE status IN ('queued','running') AND cancel_requested=1
        """, (now, now))
        await db.execute(f"""
            UPDATE {table} SET status='queued', started_at=NULL, updated_at=?
            WHERE status='running' AND cancel_requested=0
        """, (now,))
    await db.commit()
