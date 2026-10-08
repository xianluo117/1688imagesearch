"""Persistent global protection state; no timers or request execution here."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SchedulerState:
    state: Literal["normal", "cooldown", "paused"]
    reason: str | None
    until: float | None
    updated_at: float


class SchedulerStoreMixin:
    async def _scheduler_in_transaction(self, db, now: float) -> SchedulerState:
        await db.execute("""
            UPDATE queue_scheduler SET state='normal', reason=NULL, until=NULL, updated_at=?
            WHERE singleton=1 AND state='cooldown' AND until<=?
        """, (now, now))
        cursor = await db.execute("SELECT state,reason,until,updated_at FROM queue_scheduler WHERE singleton=1")
        return SchedulerState(*await cursor.fetchone())

    async def get_scheduler_state(self, *, now: float | None = None) -> SchedulerState:
        now = time.time() if now is None else now
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._scheduler_in_transaction(db, now)
            await db.commit()
            return state

    async def set_cooldown(self, reason: str, *, until: float) -> SchedulerState:
        """Extend (never shorten) cooldown; never downgrade a persistent pause."""
        now = time.time()
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._scheduler_in_transaction(db, now)
            if current.state != "paused" and until > now and (current.until is None or until > current.until):
                await db.execute("""
                    UPDATE queue_scheduler SET state='cooldown', reason=?, until=?, updated_at=?
                    WHERE singleton=1
                """, (reason, until, now))
            state = await self._scheduler_in_transaction(db, now)
            await db.commit()
            return state

    async def pause_scheduler(self, reason: str) -> SchedulerState:
        now = time.time()
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("""
                UPDATE queue_scheduler SET state='paused', reason=?, until=NULL, updated_at=?
                WHERE singleton=1
            """, (reason, now))
            await db.commit()
        return SchedulerState("paused", reason, None, now)

    async def resume_scheduler(self) -> SchedulerState:
        """Explicit operator action; cookie updates and startup never call this."""
        now = time.time()
        async with self._queue_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("""
                UPDATE queue_scheduler SET state='normal', reason=NULL, until=NULL, updated_at=?
                WHERE singleton=1
            """, (now,))
            await db.commit()
        return SchedulerState("normal", None, None, now)
