"""One event-loop-owned request gate, bridged from synchronous HTTP clients."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from image_search.errors import TaskCancelledError

from .protection_signals import error_signal, response_signal, result_signal, retry_after_seconds


class SchedulerStoppingError(RuntimeError):
    pass


@dataclass
class ExecutionBudget:
    timeout: float
    clock: Callable[[], float] = time.monotonic
    started: float = field(init=False)
    excluded: float = 0.0
    protection_started: float | None = None
    failure: Exception | None = None

    def __post_init__(self):
        self.started = self.clock()

    @property
    def elapsed(self) -> float:
        now = self.clock()
        waiting = 0 if self.protection_started is None else now - self.protection_started
        return max(0.0, now - self.started - self.excluded - waiting)

    def check(self) -> None:
        if self.failure is None and self.elapsed >= self.timeout:
            self.failure = TimeoutError("任务执行超时")
        if self.failure is not None:
            raise self.failure

    def fail(self, exc: Exception) -> None:
        if self.failure is None:
            self.failure = exc

    def suspend(self) -> None:
        if self.protection_started is None:
            self.protection_started = self.clock()

    def resume(self) -> None:
        if self.protection_started is not None:
            self.excluded += self.clock() - self.protection_started
            self.protection_started = None


class RequestGuard:
    """All methods except BoundRequestControl callbacks run on the owning loop.

    Protection suspends a budget only while that task is actually waiting at the
    gate. Network/download time still counts even if another task pauses globally.
    No lock is held while sleeping; all waiters can observe protection promptly.
    """

    def __init__(self, database, settings, *, clock=time.monotonic, wall_clock=time.time,
                 sleep=asyncio.sleep, uniform=random.uniform, poll_seconds: float = 0.05):
        self.database = database
        self.settings = settings
        self.clock, self.wall_clock, self.sleep, self.uniform = clock, wall_clock, sleep, uniform
        self.poll_seconds = poll_seconds
        self._next_request = 0.0
        self._lock = asyncio.Lock()
        self.stopping = False

    def bind(self, task_id: str, budget: ExecutionBudget) -> "BoundRequestControl":
        return BoundRequestControl(self, task_id, budget, asyncio.get_running_loop())

    def stop(self) -> None:
        self.stopping = True

    async def checkpoint(self, task_id: str, budget: ExecutionBudget) -> None:
        budget.check()
        if self.stopping:
            budget.fail(SchedulerStoppingError("调度器正在停止"))
        elif await self.database.is_queue_cancel_requested(task_id):
            budget.fail(TaskCancelledError("任务已取消"))
        # stop() may run while the cancellation query awaits SQLite. Recheck on
        # the owning loop before returning permission to the synchronous client.
        if self.stopping:
            budget.fail(SchedulerStoppingError("调度器正在停止"))
        budget.check()

    async def acquire(self, task_id: str, budget: ExecutionBudget) -> None:
        try:
            while True:
                await self.checkpoint(task_id, budget)
                async with self._lock:
                    state = await self.database.get_scheduler_state(now=self.wall_clock())
                    # Admission must recheck after SQLite awaits (including shutdown).
                    await self.checkpoint(task_id, budget)
                    if state.state != "normal":
                        budget.suspend()
                        delay = self.poll_seconds
                    else:
                        budget.resume()
                        budget.check()
                        delay = self._next_request - self.clock()
                        if delay <= 0:
                            self._next_request = self.clock() + self.uniform(
                                self.settings.request_interval_min_seconds,
                                self.settings.request_interval_max_seconds,
                            )
                            return
                        delay = min(delay, self.poll_seconds)
                await self.sleep(delay)
        finally:
            budget.resume()

    async def _persist(self, signal, response=None) -> None:
        if signal is None:
            return
        state, reason = signal
        async with self._lock:
            if state == "paused":
                await self.database.pause_scheduler(reason)
            else:
                now = self.wall_clock()
                headers = getattr(response, "headers", {}) or {}
                extra = retry_after_seconds(headers.get("Retry-After", headers.get("retry-after")), now)
                duration = max(self.settings.rate_limit_cooldown_seconds, extra or 0)
                await self.database.set_cooldown(reason, until=now + duration)

    async def observe_response(self, response) -> None:
        await self._persist(response_signal(response), response)

    async def observe_error(self, exc: Exception, response=None) -> None:
        attached = getattr(exc, "response", None)
        await self._persist(error_signal(exc), attached if attached is not None else response)

    async def observe_result(self, result, response=None) -> None:
        await self._persist(result_signal(result), response)


class BoundRequestControl:
    """Synchronous protocol adapter. Use only from the executor, never its loop.

    No timeout on future.result(): a persistent pause may outlive the execution
    budget. stop/cancel polling exits the coroutine without abandoning its thread.
    """

    def __init__(self, guard, task_id, budget, loop):
        self.guard, self.task_id, self.budget, self.loop = guard, task_id, budget, loop
        self._response = None

    def _call(self, method, *args):
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            raise RuntimeError("request_control callbacks must run in the executor")
        return asyncio.run_coroutine_threadsafe(method(*args), self.loop).result()

    def before_request(self) -> None:
        self._call(self.guard.acquire, self.task_id, self.budget)
        self._response = None

    def observe_response(self, response) -> None:
        # Preserve headers for business-level limiting carried by HTTP 200.
        # Do not read .text/.content: the SKU response may still be streaming.
        self._response = response
        self._call(self.guard.observe_response, response)

    def observe_error(self, exc: Exception) -> None:
        self._call(self.guard.observe_error, exc, self._response)

    def observe_result(self, result) -> None:
        self._call(self.guard.observe_result, result, self._response)
