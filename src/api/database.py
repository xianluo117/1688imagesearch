from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from .dingtalk import DingTalkConfig
from .queue_schema import initialize_queue
from .queue_store import QueueStoreMixin

TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class UploadTaskRecord:
    task_id: str
    status: TaskStatus
    image_url: str
    account_id: str
    cookie_version: int | None
    image_id: str | None
    search_page_url: str | None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None


@dataclass(frozen=True)
class ProductTaskRecord:
    task_id: str
    upload_task_id: str
    status: TaskStatus
    account_id: str
    cookie_version: int
    result: dict[str, Any] | None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None


class Database(QueueStoreMixin):
    def __init__(self, path: str | Path, *, queue_capacity: int = 100) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self.path = Path(path)
        self.queue_capacity = queue_capacity
        self.accepting = True

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_selected_at REAL NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS cookie_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL DEFAULT 'default',
                    encrypted_payload BLOB NOT NULL,
                    cookie_count INTEGER NOT NULL,
                    exported_at TEXT,
                    uploaded_at REAL NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
                );

                INSERT OR IGNORE INTO accounts(account_id, display_name, enabled, created_at, updated_at)
                    VALUES ('default', '默认账号', 1, 0, 0);

                DROP TABLE IF EXISTS search_tasks;

                CREATE TABLE IF NOT EXISTS upload_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                    image_url TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT 'default',
                    cookie_version INTEGER,
                    image_id TEXT,
                    search_page_url TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    FOREIGN KEY(cookie_version) REFERENCES cookie_versions(version)
                );

                CREATE INDEX IF NOT EXISTS idx_upload_tasks_status_created
                    ON upload_tasks(status, created_at);

                CREATE TABLE IF NOT EXISTS product_tasks (
                    task_id TEXT PRIMARY KEY,
                    upload_task_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                    account_id TEXT NOT NULL DEFAULT 'default',
                    cookie_version INTEGER NOT NULL,
                    result_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    FOREIGN KEY(upload_task_id) REFERENCES upload_tasks(task_id),
                    FOREIGN KEY(cookie_version) REFERENCES cookie_versions(version)
                );

                CREATE INDEX IF NOT EXISTS idx_product_tasks_status_created
                    ON product_tasks(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_product_tasks_upload
                    ON product_tasks(upload_task_id, created_at);
                """
            )
            for table in ("upload_tasks", "product_tasks"):
                cursor = await db.execute(f"PRAGMA table_info({table})")
                columns = {row[1] for row in await cursor.fetchall()}
                if "account_id" not in columns:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'")
                await db.execute(f"UPDATE {table} SET account_id='default' WHERE account_id IS NULL OR account_id=''")
            cursor = await db.execute("PRAGMA table_info(cookie_versions)")
            cookie_columns = {row[1] for row in await cursor.fetchall()}
            if "account_id" not in cookie_columns:
                await db.execute("ALTER TABLE cookie_versions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'")
            await db.execute("UPDATE cookie_versions SET account_id='default' WHERE account_id IS NULL OR account_id='' ")
            await db.execute("DROP INDEX IF EXISTS idx_cookie_active")
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cookie_active ON cookie_versions(account_id) WHERE is_active = 1")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_cookie_account ON cookie_versions(account_id, uploaded_at DESC)")
            await db.execute("INSERT OR IGNORE INTO accounts(account_id, display_name, enabled, created_at, updated_at, last_selected_at) VALUES ('default', '默认账号', 1, 0, 0, 0)")
            cursor = await db.execute("PRAGMA table_info(accounts)")
            account_columns = {row[1] for row in await cursor.fetchall()}
            if "last_selected_at" not in account_columns:
                await db.execute("ALTER TABLE accounts ADD COLUMN last_selected_at REAL NOT NULL DEFAULT 0")
            await db.commit()
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS dingtalk_config (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    access_token TEXT NOT NULL,
                    secret TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dingtalk_alerts (
                    reason TEXT PRIMARY KEY,
                    last_sent_at REAL NOT NULL
                );
                """
            )
            db.row_factory = aiosqlite.Row
            await initialize_queue(db, now)

    async def get_dingtalk_config(self) -> DingTalkConfig | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT access_token, secret, updated_at FROM dingtalk_config WHERE singleton=1"
            )
            row = await cursor.fetchone()
        return DingTalkConfig(*row) if row else None

    async def save_dingtalk_config(self, access_token: str, secret: str) -> DingTalkConfig:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO dingtalk_config(singleton, access_token, secret, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    access_token=excluded.access_token,
                    secret=excluded.secret,
                    updated_at=excluded.updated_at
                """,
                (access_token, secret, now),
            )
            await db.commit()
        return DingTalkConfig(access_token, secret, now)

    async def claim_dingtalk_alert(self, reason: str, cutoff: float, now: float) -> bool:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT last_sent_at FROM dingtalk_alerts WHERE reason=?", (reason,)
            )
            row = await cursor.fetchone()
            if row and row[0] > cutoff:
                await db.rollback()
                return False
            await db.execute(
                """
                INSERT INTO dingtalk_alerts(reason, last_sent_at) VALUES (?, ?)
                ON CONFLICT(reason) DO UPDATE SET last_sent_at=excluded.last_sent_at
                """,
                (reason, now),
            )
            await db.commit()
            return True

    async def release_dingtalk_alert(self, reason: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM dingtalk_alerts WHERE reason=?", (reason,))
            await db.commit()

    async def list_dingtalk_alerts(self) -> list[dict[str, float | str]]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "SELECT reason, last_sent_at FROM dingtalk_alerts ORDER BY reason"
            )
            rows = await cursor.fetchall()
        return [{"reason": row[0], "last_sent_at": row[1]} for row in rows]

    async def store_cookie(
        self,
        encrypted_payload: bytes,
        *,
        cookie_count: int,
        exported_at: str | None,
        account_id: str = "default",
    ) -> int:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("INSERT OR IGNORE INTO accounts(account_id, display_name, enabled, created_at, updated_at, last_selected_at) VALUES (?, ?, 1, ?, ?, 0)", (account_id, account_id, now, now))
            await db.execute("UPDATE cookie_versions SET is_active=0 WHERE account_id=? AND is_active=1", (account_id,))
            cursor = await db.execute(
                """
                INSERT INTO cookie_versions(
                    account_id, encrypted_payload, cookie_count, exported_at, uploaded_at, is_active
                ) VALUES (?, ?, ?, ?, ?, 1)
                """,
                (account_id, encrypted_payload, cookie_count, exported_at, now),
            )
            version = int(cursor.lastrowid)
            await db.commit()
            return version

    async def get_active_cookie(self, account_id: str = "default") -> tuple[int, bytes, int, float] | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """
                SELECT version, encrypted_payload, cookie_count, uploaded_at
                FROM cookie_versions WHERE account_id=? AND is_active=1 LIMIT 1
                """,
                (account_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row[0]), bytes(row[1]), int(row[2]), float(row[3])

    async def list_accounts(self, *, enabled_only: bool = False) -> list[dict[str, object]]:
        async with aiosqlite.connect(self.path) as db:
            query = "SELECT account_id, display_name, enabled, last_selected_at FROM accounts"
            if enabled_only:
                query += " WHERE enabled=1"
            query += " ORDER BY last_selected_at, account_id"
            cursor = await db.execute(query)
            return [{"account_id": row[0], "display_name": row[1], "enabled": bool(row[2]), "last_selected_at": row[3]}
                    for row in await cursor.fetchall()]

    async def ensure_account(self, account_id: str, display_name: str | None = None) -> None:
        account_id = account_id.strip()
        if not account_id or len(account_id) > 128:
            raise ValueError("account_id 无效")
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                INSERT INTO accounts(account_id, display_name, enabled, created_at, updated_at, last_selected_at)
                VALUES (?, ?, 1, ?, ?, 0)
                ON CONFLICT(account_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at
            """, (account_id, display_name or account_id, now, now))
            await db.commit()

    async def select_account(self, account_id: str | None = None) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            if account_id:
                cursor = await db.execute("""
                    SELECT a.account_id FROM accounts a
                    JOIN cookie_versions c ON c.account_id=a.account_id AND c.is_active=1
                    WHERE a.account_id=? AND a.enabled=1
                """, (account_id,))
            else:
                cursor = await db.execute("""
                    SELECT a.account_id FROM accounts a
                    JOIN cookie_versions c ON c.account_id=a.account_id AND c.is_active=1
                    WHERE a.enabled=1 ORDER BY a.last_selected_at, a.account_id LIMIT 1
                """)
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            now = time.time()
            await db.execute("UPDATE accounts SET last_selected_at=?, updated_at=? WHERE account_id=?", (now, now, row[0]))
            await db.commit()
            return str(row[0])

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

    async def create_upload_task(
        self, image_url: str, *, max_active: int | None = None,
        idempotency_key: str | None = None, account_id: str | None = None,
    ) -> str | None:
        task_id, _ = await self._enqueue(
            "upload", {"image_url": image_url}, max_active=max_active,
            idempotency_key=idempotency_key, account_id=account_id,
        )
        return task_id

    async def create_product_task(
        self, upload_task_id: str, *, max_active: int | None = None,
        idempotency_key: str | None = None, account_id: str | None = None,
    ) -> tuple[str | None, str | None]:
        return await self._enqueue(
            "product", {"upload_task_id": upload_task_id}, max_active=max_active,
            idempotency_key=idempotency_key, account_id=account_id,
        )

    async def claim_upload_task(self) -> UploadTaskRecord | None:
        # Legacy test compatibility only; production uses claim_next_task exclusively.
        task = await self._claim_queue_task(kind="upload", max_running=None)
        return await self.get_upload_task(task.task_id) if task else None

    async def claim_product_task(self) -> ProductTaskRecord | None:
        # No independent production worker pools may consume these adapters.
        task = await self._claim_queue_task(kind="product", max_running=None)
        return await self.get_product_task(task.task_id) if task else None

    async def complete_upload_task(
        self,
        task_id: str,
        *,
        image_id: str,
        search_page_url: str,
    ) -> TaskStatus:
        return await self._finish_task(
            "upload_tasks",
            task_id,
            success_values=(image_id, search_page_url),
        )

    async def complete_product_task(self, task_id: str, result: dict[str, Any]) -> TaskStatus:
        return await self._finish_task(
            "product_tasks",
            task_id,
            success_values=(json.dumps(result, ensure_ascii=False),),
        )

    async def _finish_task(
        self,
        table: str,
        task_id: str,
        *,
        success_values: tuple[Any, ...],
    ) -> TaskStatus:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                f"SELECT cancel_requested FROM {table} WHERE task_id=? AND status='running'",
                (task_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                current = await self._get_status(table, task_id)
                return current or "failed"
            if bool(row[0]):
                await db.execute(
                    f"""
                    UPDATE {table} SET status='cancelled', error_code='TASK_CANCELLED',
                        error_message='任务已取消', finished_at=?, updated_at=? WHERE task_id=?
                    """,
                    (now, now, task_id),
                )
                await db.commit()
                return "cancelled"
            if table == "upload_tasks":
                await db.execute(
                    """
                    UPDATE upload_tasks SET status='succeeded', image_id=?, search_page_url=?,
                        error_code=NULL, error_message=NULL, finished_at=?, updated_at=? WHERE task_id=?
                    """,
                    (*success_values, now, now, task_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE product_tasks SET status='succeeded', result_json=?, error_code=NULL,
                        error_message=NULL, finished_at=?, updated_at=? WHERE task_id=?
                    """,
                    (*success_values, now, now, task_id),
                )
            await db.commit()
            return "succeeded"

    async def fail_upload_task(self, task_id: str, code: str, message: str) -> TaskStatus:
        return await self._fail_task("upload_tasks", task_id, code, message)

    async def fail_product_task(self, task_id: str, code: str, message: str) -> TaskStatus:
        return await self._fail_task("product_tasks", task_id, code, message)

    async def _fail_task(self, table: str, task_id: str, code: str, message: str) -> TaskStatus:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                f"SELECT cancel_requested FROM {table} WHERE task_id=? AND status='running'",
                (task_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                current = await self._get_status(table, task_id)
                return current or "failed"
            cancelled = bool(row[0]) or code == "TASK_CANCELLED"
            status: TaskStatus = "cancelled" if cancelled else "failed"
            final_code = "TASK_CANCELLED" if cancelled else code
            final_message = "任务已取消" if cancelled else message[:1000]
            await db.execute(
                f"""
                UPDATE {table} SET status=?, error_code=?, error_message=?,
                    finished_at=?, updated_at=? WHERE task_id=?
                """,
                (status, final_code, final_message, now, now, task_id),
            )
            await db.commit()
            return status

    async def cancel_upload_task(self, task_id: str) -> UploadTaskRecord | None:
        await self._cancel_task("upload_tasks", task_id)
        return await self.get_upload_task(task_id)

    async def cancel_product_task(self, task_id: str) -> ProductTaskRecord | None:
        await self._cancel_task("product_tasks", task_id)
        return await self.get_product_task(task_id)

    async def _cancel_task(self, table: str, task_id: str) -> None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(f"SELECT status FROM {table} WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
            if row is None or row[0] in TERMINAL_STATUSES:
                await db.rollback()
                return
            if row[0] == "queued":
                await db.execute(
                    f"""
                    UPDATE {table} SET status='cancelled', cancel_requested=1,
                        error_code='TASK_CANCELLED', error_message='任务已取消',
                        finished_at=?, updated_at=? WHERE task_id=?
                    """,
                    (now, now, task_id),
                )
            else:
                await db.execute(
                    f"UPDATE {table} SET cancel_requested=1, updated_at=? WHERE task_id=?",
                    (now, task_id),
                )
            await db.commit()

    async def is_upload_cancel_requested(self, task_id: str) -> bool:
        return await self._is_cancel_requested("upload_tasks", task_id)

    async def is_product_cancel_requested(self, task_id: str) -> bool:
        return await self._is_cancel_requested("product_tasks", task_id)

    async def _is_cancel_requested(self, table: str, task_id: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                f"SELECT cancel_requested, status FROM {table} WHERE task_id=?",
                (task_id,),
            )
            row = await cursor.fetchone()
        return row is None or bool(row[0]) or row[1] == "cancelled"

    async def get_upload_task(self, task_id: str) -> UploadTaskRecord | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM upload_tasks WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return UploadTaskRecord(
            task_id=row["task_id"],
            status=row["status"],
            image_url=row["image_url"],
            account_id=row["account_id"],
            cookie_version=row["cookie_version"],
            image_id=row["image_id"],
            search_page_url=row["search_page_url"],
            cancel_requested=bool(row["cancel_requested"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    async def get_product_task(self, task_id: str) -> ProductTaskRecord | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM product_tasks WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        return ProductTaskRecord(
            task_id=row["task_id"],
            upload_task_id=row["upload_task_id"],
            status=row["status"],
            account_id=row["account_id"],
            cookie_version=row["cookie_version"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            cancel_requested=bool(row["cancel_requested"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    async def _get_status(self, table: str, task_id: str) -> TaskStatus | None:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(f"SELECT status FROM {table} WHERE task_id=?", (task_id,))
            row = await cursor.fetchone()
        return row[0] if row else None

    async def upload_task_counts(self) -> dict[str, int]:
        return await self._task_counts("upload_tasks")

    async def product_task_counts(self) -> dict[str, int]:
        return await self._task_counts("product_tasks")

    async def _task_counts(self, table: str) -> dict[str, int]:
        counts = {status: 0 for status in ("queued", "running", "succeeded", "failed", "cancelled")}
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status")
            for status, count in await cursor.fetchall():
                counts[str(status)] = int(count)
        return counts

    async def cleanup_tasks(self, older_than: float) -> int:
        return await self.cleanup_queue_tasks(older_than)
