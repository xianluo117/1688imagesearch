"""Single-process, three-kind FIFO dispatcher with operation-owned execution slots."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from image_search.errors import TaskCancelledError

from .queue_store import TERMINAL
from .request_guard import ExecutionBudget, RequestGuard, SchedulerStoppingError
from .worker import QueueTaskExecutor, classify_error

LOGGER = logging.getLogger(__name__)


class UnifiedQueueScheduler:
    """Start only once per Database; never run legacy pools alongside this service.

    execute_sku_sync(url, records, request_control) is an optional synchronous test
    hook returning SkuResult or its raw to_dict(). It always runs in our sole pool.
    Waiting/cancelling an HTTP waiter does not own or cancel a scheduled operation.
    """

    def __init__(self, database, cookie_store, settings, *, execute_sku_sync=None,
                 task_executor=None, request_guard=None, poll_seconds: float = 0.05):
        self.database, self.settings = database, settings
        self.database.queue_capacity = settings.max_queued_tasks
        self.executor = task_executor or QueueTaskExecutor(database, cookie_store, settings)
        self.execute_sku_sync = execute_sku_sync or self.executor.sku_sync
        self.guard = request_guard or RequestGuard(database, settings)
        self.poll_seconds = poll_seconds
        self._pool = None
        self._dispatcher = None
        self._drain_task = None
        self._jobs = set()
        self._closing = False

    async def start(self) -> None:
        if self._dispatcher is not None:
            return
        if self._closing:
            raise RuntimeError("已关闭的调度器不能重新启动")
        owner = getattr(self.database, "_scheduler_owner", None)
        if owner is not None and owner is not self:
            raise RuntimeError("该 Database 已有统一调度器")
        self.database._scheduler_owner = self
        self.database.accepting = True
        self._pool = ThreadPoolExecutor(max_workers=self.settings.global_worker_count,
                                        thread_name_prefix="1688-unified")
        self._dispatcher = asyncio.create_task(self._dispatch(), name="1688-dispatcher")

    async def stop(self) -> None:
        # No cancellation of dispatcher, operation, or executor futures.
        if self._drain_task is None:
            self._closing = True
            self.database.accepting = False
            self.guard.stop()
            self._drain_task = asyncio.create_task(self._drain(), name="1688-drain")
        await asyncio.shield(self._drain_task)

    async def _drain(self) -> None:
        if self._dispatcher is not None:
            await self._dispatcher
        if self._jobs:
            await asyncio.gather(*tuple(self._jobs))
        if self._pool is not None:
            # Every submitted future has actually returned before shutdown.
            self._pool.shutdown(wait=True)
        if getattr(self.database, "_scheduler_owner", None) is self:
            self.database._scheduler_owner = None

    async def _dispatch(self) -> None:
        while not self._closing:
            try:
                if len(self._jobs) < self.settings.global_worker_count:
                    task = await self.database.claim_next_task(max_running=self.settings.global_worker_count)
                    if task is not None:
                        job = asyncio.create_task(self._run(task), name=f"queue-{task.task_id}")
                        self._jobs.add(job)
                        job.add_done_callback(self._finished)
                        continue
            except Exception:
                LOGGER.exception("统一队列领取失败，稍后重试")
            await asyncio.sleep(self.poll_seconds)

    def _finished(self, job):
        self._jobs.discard(job)
        if not job.cancelled() and job.exception() is not None:
            # Do not consume more work after a persistence failure. The still-running
            # DB record is intentionally left for startup recovery, never fabricated.
            LOGGER.error("统一任务落库失败", exc_info=job.exception())
            self.database.accepting = False
            self._closing = True
            self.guard.stop()

    async def _watch(self, task_id, budget, done):
        while not done.is_set():
            try:
                await self.guard.checkpoint(task_id, budget)
            except Exception as exc:
                budget.fail(exc)
                return
            await asyncio.sleep(self.poll_seconds)

    async def _run(self, task) -> None:
        timeout = getattr(self.settings, f"{task.kind}_task_timeout_seconds")
        budget = ExecutionBudget(timeout, clock=self.guard.clock)
        control = self.guard.bind(task.task_id, budget)
        done = asyncio.Event()
        watcher = asyncio.create_task(self._watch(task.task_id, budget, done))

        async def run_sync(callback, *args):
            await self.guard.checkpoint(task.task_id, budget)
            future = asyncio.get_running_loop().run_in_executor(self._pool, partial(callback, *args))
            return await asyncio.shield(future)

        result, failure = None, None
        try:
            await self.guard.checkpoint(task.task_id, budget)
            result = await self.executor.execute(task, control, run_sync, self.execute_sku_sync)
            if hasattr(result, "to_dict"):
                result = result.to_dict()
            await self.guard.observe_result(result)
            await self.guard.checkpoint(task.task_id, budget)
        except Exception as exc:
            failure = exc
            await self.guard.observe_error(exc)
        finally:
            done.set()
            await watcher
        # The operation has ended. A late success never overrides a latched timeout.
        failure = budget.failure or failure
        if failure is not None:
            code, message = classify_error(failure, task_kind=task.kind)
            if isinstance(failure, SchedulerStoppingError):
                code, message = "SCHEDULER_STOPPING", str(failure)
            await self.database.finish_queue_task(
                task.task_id, status="cancelled" if isinstance(failure, TaskCancelledError) else "failed",
                error_code=code, error_message=message,
            )
        elif task.kind == "sku":
            await self.database.complete_sku_task(task.task_id, result)
        else:
            await self.database.finish_queue_task(task.task_id, status="succeeded", result=result)

    async def wait_for_task(self, task_id: str, *, timeout: float | None = None):
        """Return the raw QueueTaskRecord. TimeoutError affects only this waiter."""
        async def wait():
            while True:
                # Cancellation during aiosqlite.connect can abandon its connection
                # before the store's context manager is entered. Drain the read.
                read = asyncio.create_task(self.database.get_queue_task(task_id))
                try:
                    task = await asyncio.shield(read)
                except asyncio.CancelledError:
                    await read
                    raise
                if task is None or task.status in TERMINAL:
                    return task
                await asyncio.sleep(self.poll_seconds)
        if timeout is None:
            return await wait()
        return await asyncio.wait_for(wait(), timeout)

    async def resume(self):
        """Explicit operator action only; start() and cookie changes never call it."""
        return await self.database.resume_scheduler()

    async def snapshot(self):
        return await self.database.queue_snapshot()
