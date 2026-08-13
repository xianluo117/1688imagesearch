from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiosqlite

TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class UploadTaskRecord:
    task_id: str
    status: TaskStatus
    image_url: str
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
    cookie_version: int
    result: dict[str, Any] | None
    cancel_requested: bool
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
        now = time.time()
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

                DROP TABLE IF EXISTS search_tasks;

                CREATE TABLE IF NOT EXISTS upload_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
                    image_url TEXT NOT NULL,
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
                await db.execute(
                    f"""
                    UPDATE {table}
                    SET status='cancelled', finished_at=?, updated_at=?
                    WHERE status='running' AND cancel_requested=1
                    """,
                    (now, now),
                )
                await db.execute(
                    f"""
                    UPDATE {table}
                    SET status='queued', started_at=NULL, updated_at=?
                    WHERE status='running' AND cancel_requested=0
                    """,
                    (now,),
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

    async def create_upload_task(self, image_url: str, *, max_active: int) -> str | None:
        now = time.time()
        task_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT COUNT(*) FROM upload_tasks WHERE status IN ('queued','running')"
            )
            if int((await cursor.fetchone())[0]) >= max_active:
                await db.rollback()
                return None
            await db.execute(
                """
                INSERT INTO upload_tasks(task_id, status, image_url, created_at, updated_at)
                VALUES (?, 'queued', ?, ?, ?)
                """,
                (task_id, image_url, now, now),
            )
            await db.commit()
        return task_id

    async def create_product_task(
        self,
        upload_task_id: str,
        *,
        max_active: int,
    ) -> tuple[str | None, str | None]:
        now = time.time()
        task_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT status, cookie_version, image_id FROM upload_tasks WHERE task_id=?",
                (upload_task_id,),
            )
            upload = await cursor.fetchone()
            if upload is None:
                await db.rollback()
                return None, "UPLOAD_TASK_NOT_FOUND"
            if upload["status"] == "cancelled":
                await db.rollback()
                return None, "UPLOAD_TASK_CANCELLED"
            if upload["status"] == "failed":
                await db.rollback()
                return None, "UPLOAD_TASK_FAILED"
            if upload["status"] != "succeeded" or not upload["image_id"] or upload["cookie_version"] is None:
                await db.rollback()
                return None, "UPLOAD_TASK_NOT_READY"
            count_cursor = await db.execute(
                "SELECT COUNT(*) FROM product_tasks WHERE status IN ('queued','running')"
            )
            if int((await count_cursor.fetchone())[0]) >= max_active:
                await db.rollback()
                return None, "QUEUE_FULL"
            await db.execute(
                """
                INSERT INTO product_tasks(
                    task_id, upload_task_id, status, cookie_version, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (task_id, upload_task_id, int(upload["cookie_version"]), now, now),
            )
            await db.commit()
        return task_id, None

    async def claim_upload_task(self) -> UploadTaskRecord | None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT task_id FROM upload_tasks WHERE status='queued' ORDER BY created_at LIMIT 1"
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
                UPDATE upload_tasks
                SET status='running', cookie_version=?, started_at=?, updated_at=?
                WHERE task_id=? AND status='queued'
                """,
                (cookie_version, now, now, row[0]),
            )
            await db.commit()
        return await self.get_upload_task(str(row[0]))

    async def claim_product_task(self) -> ProductTaskRecord | None:
        now = time.time()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT task_id FROM product_tasks WHERE status='queued' ORDER BY created_at LIMIT 1"
            )
            row = await cursor.fetchone()
            if row is None:
                await db.rollback()
                return None
            await db.execute(
                """
                UPDATE product_tasks SET status='running', started_at=?, updated_at=?
                WHERE task_id=? AND status='queued'
                """,
                (now, now, row[0]),
            )
            await db.commit()
        return await self.get_product_task(str(row[0]))

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
        deleted = 0
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")
            product_cursor = await db.execute(
                """
                DELETE FROM product_tasks
                WHERE status IN ('succeeded','failed','cancelled') AND finished_at < ?
                """,
                (older_than,),
            )
            deleted += int(product_cursor.rowcount)
            upload_cursor = await db.execute(
                """
                DELETE FROM upload_tasks
                WHERE status IN ('succeeded','failed','cancelled') AND finished_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM product_tasks WHERE product_tasks.upload_task_id=upload_tasks.task_id
                  )
                """,
                (older_than,),
            )
            deleted += int(upload_cursor.rowcount)
            await db.commit()
        return deleted
