"""SQLite queue transactions shared by image search and SKU tasks.

One Database instance owns admission in a single-process deployment. Set accepting
False before draining. initialize() is startup recovery, never a live refresh.
Completion methods must only be called after the underlying operation has ended.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import aiosqlite

from .queue_schema import encode
from .queue_scheduler_store import SchedulerStoreMixin

TaskKind = Literal["upload", "product", "sku"]
TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL = ("succeeded", "failed", "cancelled")


class IdempotencyConflict(ValueError):
    """A global idempotency key was already submitted with different parameters."""


class QueueNotAccepting(RuntimeError):
    """The process is draining; no new submissions are accepted."""


@dataclass(frozen=True)
class QueueTaskRecord:
    seq: int
    task_id: str
    kind: TaskKind
    status: TaskStatus
    payload: dict[str, Any]
    dependency_id: str | None
    result: dict[str, Any] | None
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None


def record(row: aiosqlite.Row) -> QueueTaskRecord:
    values = dict(row)
    values["payload"] = json.loads(values.pop("payload_json"))
    result = values.pop("result_json")
    values["result"] = json.loads(result) if result is not None else None
    values["cancel_requested"] = bool(values["cancel_requested"])
    return QueueTaskRecord(**values)


class QueueStoreMixin(SchedulerStoreMixin):
    accepting: bool = True
    queue_capacity: int = 100

    @asynccontextmanager
    async def _queue_connection(self):
        async with aiosqlite.connect(self.path, timeout=30) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def _enqueue(
        self, kind: TaskKind, payload: dict[str, Any], *,
        max_active: int | None, idempotency_key: str | None,
    ) -> tuple[str | None, str | None]:
        canonical = encode(payload)
        limit = self.queue_capacity if max_active is None else min(self.queue_capacity, max_active)
        if limit < 1:
            raise ValueError("max_active must be positive")
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            if not self.accepting:
                raise QueueNotAccepting("queue is stopping")
            if idempotency_key is not None:
                c = await db.execute("SELECT * FROM queue_idempotency WHERE key=?", (idempotency_key,))
                previous = await c.fetchone()
                if previous is not None:
                    if previous["kind"] != kind or previous["payload_json"] != canonical:
                        raise IdempotencyConflict(idempotency_key)
                    return previous["task_id"], None
            cookie_version = None
            dependency = payload["upload_task_id"] if kind == "product" else None
            if dependency is not None:
                c = await db.execute("SELECT * FROM upload_tasks WHERE task_id=?", (dependency,))
                upload = await c.fetchone()
                if upload is None:
                    return None, "UPLOAD_TASK_NOT_FOUND"
                if upload["status"] in ("cancelled", "failed"):
                    return None, "UPLOAD_TASK_" + upload["status"].upper()
                if upload["status"] != "succeeded" or not upload["image_id"] or upload["cookie_version"] is None:
                    return None, "UPLOAD_TASK_NOT_READY"
                cookie_version = upload["cookie_version"]
            c = await db.execute("SELECT COUNT(*) FROM queue_tasks WHERE status IN ('queued','running')")
            if (await c.fetchone())[0] >= limit:
                return None, "QUEUE_FULL"
            now, task_id = time.time(), str(uuid.uuid4())
            await db.execute("""
                INSERT INTO queue_tasks(task_id,kind,status,payload_json,dependency_id,created_at,updated_at)
                VALUES (?,?,'queued',?,?,?,?)
            """, (task_id, kind, canonical, dependency, now, now))
            if kind == "upload":
                await db.execute("""
                    INSERT INTO upload_tasks(task_id,status,image_url,created_at,updated_at)
                    VALUES (?,'queued',?,?,?)
                """, (task_id, payload["image_url"], now, now))
            elif kind == "product":
                await db.execute("""
                    INSERT INTO product_tasks(task_id,upload_task_id,status,cookie_version,created_at,updated_at)
                    VALUES (?,?,'queued',?,?,?)
                """, (task_id, dependency, cookie_version, now, now))
            if idempotency_key is not None:
                await db.execute("INSERT INTO queue_idempotency VALUES (?,?,?,?)",
                                 (idempotency_key, kind, canonical, task_id))
            # Catch shutdown occurring while this transaction was awaiting SQLite.
            if not self.accepting:
                raise QueueNotAccepting("queue is stopping")
            await db.commit()
            return task_id, None

    async def create_sku_task(
        self, product_url: str, *, max_active: int | None = None,
        idempotency_key: str | None = None,
    ) -> str | None:
        task_id, _ = await self._enqueue("sku", {"product_url": product_url},
                                         max_active=max_active, idempotency_key=idempotency_key)
        return task_id

    async def get_queue_task(self, task_id: str) -> QueueTaskRecord | None:
        async with self._queue_connection() as db:
            c = await db.execute("SELECT * FROM queue_tasks WHERE task_id=?", (task_id,))
            row = await c.fetchone()
            return record(row) if row is not None else None

    async def claim_next_task(self, *, max_running: int = 1) -> QueueTaskRecord | None:
        """Atomically claim the first ready seq, sharing a global execution limit."""
        return await self._claim_queue_task(max_running=max_running)

    async def _claim_queue_task(
        self, *, max_running: int | None, kind: TaskKind | None = None,
    ) -> QueueTaskRecord | None:
        if max_running is not None and max_running < 1:
            raise ValueError("max_running must be positive")
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            if not self.accepting:
                return None
            now = time.time()
            state = await self._scheduler_in_transaction(db, now)
            if state.state != "normal":
                await db.commit()
                return None
            c = await db.execute("SELECT COUNT(*) FROM queue_tasks WHERE status='running'")
            if max_running is not None and (await c.fetchone())[0] >= max_running:
                await db.commit()
                return None
            c = await db.execute("""
                SELECT q.* FROM queue_tasks q
                WHERE q.status='queued' AND q.cancel_requested=0 AND (? IS NULL OR q.kind=?)
                  AND (q.dependency_id IS NULL OR EXISTS (
                      SELECT 1 FROM queue_tasks p WHERE p.task_id=q.dependency_id AND p.status='succeeded'))
                ORDER BY q.seq LIMIT 1
            """, (kind, kind))
            row = await c.fetchone()
            if row is None:
                await db.commit()
                return None
            task_id = row["task_id"]
            if row["kind"] == "upload":
                await db.execute("""
                    UPDATE upload_tasks SET status='running', started_at=?, updated_at=?,
                        cookie_version=(SELECT version FROM cookie_versions WHERE is_active=1)
                    WHERE task_id=? AND status='queued'
                """, (now, now, task_id))
            elif row["kind"] == "product":
                await db.execute("""
                    UPDATE product_tasks SET status='running', started_at=?, updated_at=?
                    WHERE task_id=? AND status='queued'
                """, (now, now, task_id))
            else:
                await db.execute("""
                    UPDATE queue_tasks SET status='running', started_at=?, updated_at=? WHERE task_id=?
                """, (now, now, task_id))
            c = await db.execute("SELECT * FROM queue_tasks WHERE task_id=?", (task_id,))
            claimed = record(await c.fetchone())
            if not self.accepting:
                await db.rollback()
                return None
            await db.commit()
            return claimed

    async def cancel_queue_task(self, task_id: str) -> QueueTaskRecord | None:
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            c = await db.execute("SELECT * FROM queue_tasks WHERE task_id=?", (task_id,))
            row = await c.fetchone()
            if row is None:
                return None
            if row["status"] not in TERMINAL:
                table = {"upload": "upload_tasks", "product": "product_tasks", "sku": "queue_tasks"}[row["kind"]]
                now = time.time()
                if row["status"] == "queued":
                    await db.execute(f"""
                        UPDATE {table} SET status='cancelled', cancel_requested=1,
                            error_code='TASK_CANCELLED', error_message='任务已取消',
                            finished_at=?, updated_at=? WHERE task_id=?
                    """, (now, now, task_id))
                else:
                    await db.execute(f"UPDATE {table} SET cancel_requested=1, updated_at=? WHERE task_id=?",
                                     (now, task_id))
            c = await db.execute("SELECT * FROM queue_tasks WHERE task_id=?", (task_id,))
            cancelled = record(await c.fetchone())
            await db.commit()
            return cancelled

    async def is_queue_cancel_requested(self, task_id: str) -> bool:
        task = await self.get_queue_task(task_id)
        return task is None or task.cancel_requested or task.status == "cancelled"

    async def complete_sku_task(self, task_id: str, result: dict[str, Any]) -> TaskStatus | None:
        """Pass SkuResult.to_dict(), NOT an HTTP v1/v2 presentation payload."""
        success = result.get("status") in ("success", "partial_success") and bool(result.get("skus"))
        return await self.finish_queue_task(
            task_id, status="succeeded" if success else "failed", result=result,
            error_code=None if success else str(result.get("status", "SKU_QUERY_FAILED")),
            error_message=None if success else str(result.get("reason", "")),
        )

    async def finish_queue_task(
        self, task_id: str, *, status: Literal["succeeded", "failed", "cancelled"],
        result: dict[str, Any] | None = None, error_code: str | None = None,
        error_message: str | None = None,
    ) -> TaskStatus | None:
        """Acknowledge actual operation end, not waiter cancellation or timeout.

        Cancellation wins. Non-running and terminal records are returned unchanged.
        SKU success is derived from its original business result, not caller status.
        """
        if status not in TERMINAL:
            raise ValueError("completion requires a terminal status")
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            c = await db.execute("SELECT * FROM queue_tasks WHERE task_id=?", (task_id,))
            row = await c.fetchone()
            if row is None or row["status"] != "running":
                return row["status"] if row else None
            if row["cancel_requested"] or status == "cancelled":
                status, result = "cancelled", None
                error_code, error_message = "TASK_CANCELLED", "任务已取消"
            elif row["kind"] == "sku" and result is not None:
                success = result.get("status") in ("success", "partial_success") and bool(result.get("skus"))
                status = "succeeded" if success else "failed"
                error_code = None if success else str(result.get("status", "SKU_QUERY_FAILED"))
                error_message = None if success else str(result.get("reason", ""))
            elif row["kind"] == "sku" and status == "succeeded":
                raise ValueError("SKU success requires an original business result")
            now = time.time()
            values = (status, error_code, error_message, now, now, task_id)
            if row["kind"] == "sku":
                await db.execute("""
                    UPDATE queue_tasks SET result_json=?, status=?, error_code=?, error_message=?,
                        finished_at=?, updated_at=? WHERE task_id=?
                """, (encode(result) if result is not None else None, *values))
            else:
                table = "upload_tasks" if row["kind"] == "upload" else "product_tasks"
                if status == "succeeded":
                    if result is None:
                        raise ValueError("success requires a business result")
                    if row["kind"] == "upload":
                        await db.execute("UPDATE upload_tasks SET image_id=?, search_page_url=? WHERE task_id=?",
                                         (result["image_id"], result["search_page_url"], task_id))
                    else:
                        await db.execute("UPDATE product_tasks SET result_json=? WHERE task_id=?", (encode(result), task_id))
                await db.execute(f"""
                    UPDATE {table} SET status=?, error_code=?, error_message=?, finished_at=?, updated_at=?
                    WHERE task_id=?
                """, values)
            await db.commit()
            return status

    async def queue_task_counts(self) -> dict[str, int]:
        return await self._task_counts("queue_tasks")

    async def queue_snapshot(self) -> dict[str, Any]:
        """One transaction: counts, protection state, remaining cooldown and admission."""
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            now = time.time()
            state = await self._scheduler_in_transaction(db, now)
            counts = {s: 0 for s in ("queued", "running", *TERMINAL)}
            c = await db.execute("SELECT status,COUNT(*) FROM queue_tasks GROUP BY status")
            counts.update(dict(await c.fetchall()))
            await db.commit()
            return {"counts": counts, "accepting": self.accepting, "capacity": self.queue_capacity,
                    "scheduler": {"state": state.state, "reason": state.reason, "until": state.until,
                                  "updated_at": state.updated_at,
                                  "cooldown_remaining": max(0, (state.until or now) - now)}}

    async def cleanup_queue_tasks(self, older_than: float) -> int:
        """Delete terminal leaves, results and their keys atomically; retain dependencies."""
        deleted = 0
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            while True:
                c = await db.execute("""
                    SELECT q.task_id,q.kind FROM queue_tasks q
                    WHERE q.status IN ('succeeded','failed','cancelled') AND q.finished_at < ?
                      AND NOT EXISTS (SELECT 1 FROM queue_tasks child WHERE child.dependency_id=q.task_id)
                      AND NOT EXISTS (SELECT 1 FROM product_tasks p WHERE p.upload_task_id=q.task_id)
                """, (older_than,))
                rows = await c.fetchall()
                if not rows:
                    break
                for row in rows:
                    if row["kind"] in ("upload", "product"):
                        table = "upload_tasks" if row["kind"] == "upload" else "product_tasks"
                        await db.execute(f"DELETE FROM {table} WHERE task_id=?", (row["task_id"],))
                    await db.execute("DELETE FROM queue_tasks WHERE task_id=?", (row["task_id"],))
                    deleted += 1
            await db.commit()
        return deleted
