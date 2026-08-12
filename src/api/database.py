from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    status: str
    image_url: str
    cookie_version: int | None
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cookie_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT,
                    encrypted_payload BLOB NOT NULL,
                    cookie_count INTEGER NOT NULL,
                    exported_at TEXT,
                    uploaded_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_cookie_active
                    ON cookie_versions(is_active) WHERE is_active = 1;

                CREATE TABLE IF NOT EXISTS search_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
                    image_url TEXT NOT NULL,
                    cookie_version INTEGER,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    FOREIGN KEY(cookie_version) REFERENCES cookie_versions(version)
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                    ON search_tasks(status, created_at);
                """
            )
            await db.execute(
                "UPDATE search_tasks SET status='queued', started_at=NULL, updated_at=? WHERE status='running'",
                (time.time(),),
            )
            await db.commit()

    async def store_cookie(
        self,
        encrypted_payload: bytes,
        *,
        cookie_count: int,
        exported_at: str | None,
    ) -> int:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE cookie_versions SET is_active=0 WHERE is_active=1")
            cursor = await db.execute(
                """
                INSERT INTO cookie_versions(
                    encrypted_payload, cookie_count, exported_at, uploaded_at, is_active
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (encrypted_payload, cookie_count, exported_at, now),
            )
            version = int(cursor.lastrowid)
            await db.commit()
            return version

    async def get_active_cookie(self) -> tuple[int, bytes, int, float] | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT version, encrypted_payload, cookie_count, uploaded_at
                FROM cookie_versions WHERE is_active=1 LIMIT 1
                """
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row[0]), bytes(row[1]), int(row[2]), float(row[3])

    async def get_cookie_version(self, version: int) -> tuple[int, bytes, int, float] | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT version, encrypted_payload, cookie_count, uploaded_at
                FROM cookie_versions WHERE version=? LIMIT 1
                """,
                (version,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row[0]), bytes(row[1]), int(row[2]), float(row[3])

    async def create_task(self, image_url: str, *, max_queued: int) -> str | None:
        now = time.time()
        task_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT COUNT(*) FROM search_tasks WHERE status IN ('queued','running')"
            )
            count = int((await cursor.fetchone())[0])
            if count >= max_queued:
                await db.rollback()
                return None
            await db.execute(
                """
                INSERT INTO search_tasks(task_id, status, image_url, created_at, updated_at)
                VALUES (?, 'queued', ?, ?, ?)
                """,
                (task_id, image_url, now, now),
            )
            await db.commit()
        return task_id

    async def claim_task(self) -> TaskRecord | None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                SELECT task_id, image_url FROM search_tasks
                WHERE status='queued' ORDER BY created_at LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            cookie_cursor = await db.execute(
                "SELECT version FROM cookie_versions WHERE is_active=1 LIMIT 1"
            )
            cookie_row = await cookie_cursor.fetchone()
            cookie_version = int(cookie_row[0]) if cookie_row else None
            await db.execute(
                """
                UPDATE search_tasks
                SET status='running', cookie_version=?, started_at=?, updated_at=?
                WHERE task_id=? AND status='queued'
                """,
                (cookie_version, now, now, row[0]),
            )
            await db.commit()
        return await self.get_task(str(row[0]))

    async def complete_task(self, task_id: str, result: dict[str, Any]) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE search_tasks SET status='succeeded', result_json=?, error_code=NULL,
                    error_message=NULL, finished_at=?, updated_at=? WHERE task_id=?
                """,
                (json.dumps(result, ensure_ascii=False), now, now, task_id),
            )
            await db.commit()

    async def fail_task(self, task_id: str, code: str, message: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE search_tasks SET status='failed', error_code=?, error_message=?,
                    finished_at=?, updated_at=? WHERE task_id=?
                """,
                (code, message[:1000], now, now, task_id),
            )
            await db.commit()

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM search_tasks WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            task_id=row["task_id"],
            status=row["status"],
            image_url=row["image_url"],
            cookie_version=row["cookie_version"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    async def task_counts(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM search_tasks GROUP BY status"
            )
            for status, count in await cursor.fetchall():
                counts[str(status)] = int(count)
        return counts

    async def cleanup_tasks(self, older_than: float) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                DELETE FROM search_tasks
                WHERE status IN ('succeeded','failed') AND finished_at < ?
                """,
                (older_than,),
            )
            await db.commit()
            return int(cursor.rowcount)
