"""Offline gate tests: fake clock for budgets, real SQLite for persistence."""
import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

from api.database import Database
from api.protection_signals import retry_after_seconds
from api.request_guard import ExecutionBudget, RequestGuard, SchedulerStoppingError
from image_search.errors import AuthenticationError, RateLimitError, RiskControlError, TaskCancelledError


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


class FakeStore:
    def __init__(self):
        self.state = "normal"
        self.cancel = False
        self.until = None
        self.clock = None

    async def is_queue_cancel_requested(self, task_id):
        return self.cancel

    async def get_scheduler_state(self, **kwargs):
        if self.until and self.clock() >= self.until:
            self.state = "normal"
        return SimpleNamespace(state=self.state)


def settings(**kwargs):
    return SimpleNamespace(**dict(request_interval_min_seconds=2,
                                  request_interval_max_seconds=4,
                                  rate_limit_cooldown_seconds=60, **kwargs))


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock, self.store = Clock(), FakeStore()
        self.store.clock = self.clock
        self.guard = RequestGuard(self.store, settings(), clock=self.clock,
                                  sleep=self.clock.sleep, uniform=lambda a, b: 3)

    async def test_shared_interval_counts_budget(self):
        first, second = ExecutionBudget(20, self.clock), ExecutionBudget(20, self.clock)
        await self.guard.acquire("a", first)
        await self.guard.acquire("b", second)
        self.assertAlmostEqual(self.clock.now, 103)
        self.assertAlmostEqual(second.elapsed, 3)

    async def test_interval_cannot_outlive_budget_or_grant_late_request(self):
        await self.guard.acquire("a", ExecutionBudget(10, self.clock))
        budget = ExecutionBudget(1, self.clock)
        with self.assertRaises(TimeoutError):
            await self.guard.acquire("b", budget)
        self.assertLess(self.clock.now, 103)
        self.clock.now += 100
        with self.assertRaises(TimeoutError):
            await self.guard.acquire("b", budget)

    async def test_cooldown_excludes_only_protection_wait(self):
        self.store.state, self.store.until = "cooldown", 200
        budget = ExecutionBudget(2, self.clock)
        await self.guard.acquire("a", budget)
        self.assertGreaterEqual(self.clock.now, 200)
        self.assertLess(budget.elapsed, 0.01)
        self.clock.now += 2
        with self.assertRaises(TimeoutError):
            budget.check()

    async def test_pause_wait_cancel_and_stop_exit_without_resume(self):
        for cancel in (True, False):
            self.store.state, self.store.cancel = "paused", False
            self.guard.stopping = False
            budget = ExecutionBudget(1, self.clock)
            waiter = asyncio.create_task(self.guard.acquire("a", budget))
            while budget.protection_started is None:
                await asyncio.sleep(0)
            self.clock.now += 500
            budget.check()
            if cancel:
                self.store.cancel = True
            else:
                self.guard.stop()
            with self.assertRaises(TaskCancelledError if cancel else SchedulerStoppingError):
                await waiter
            self.assertIsNone(budget.protection_started)

    async def test_budget_exhausted_before_pause_is_not_resurrected(self):
        budget = ExecutionBudget(1, self.clock)
        self.clock.now += 2
        self.store.state = "paused"
        with self.assertRaises(TimeoutError):
            await self.guard.acquire("a", budget)

    async def test_shutdown_during_admission_database_read_never_grants_request(self):
        reads = 0

        async def cancellation_read(task_id):
            nonlocal reads
            reads += 1
            if reads == 2:
                # stop() can run while the final admission checkpoint awaits SQLite.
                self.guard.stop()
            await asyncio.sleep(0)
            return False

        self.store.is_queue_cancel_requested = cancellation_read
        with self.assertRaises(SchedulerStoppingError):
            await self.guard.acquire("a", ExecutionBudget(20, self.clock))
        self.assertEqual(self.guard._next_request, 0)

    async def test_bound_callback_bridges_to_loop_and_rejects_loop_thread(self):
        control = self.guard.bind("a", ExecutionBudget(20, self.clock))
        with self.assertRaises(RuntimeError):
            control.before_request()
        await asyncio.to_thread(control.before_request)
        await asyncio.to_thread(control.before_request)
        self.assertAlmostEqual(self.clock.now, 103)


class PersistentSignalsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Database(Path(temp.name) / "queue.db")
        await self.store.initialize()
        self.guard = RequestGuard(self.store, settings())

    async def test_rate_retry_after_and_pause_priority_survive_restart(self):
        now = time.time()
        response = SimpleNamespace(status_code=429, headers={"Retry-After": "120"}, url="https://detail.1688.com/offer/1.html")
        await self.guard.observe_response(response)
        state = await self.store.get_scheduler_state()
        self.assertGreaterEqual(state.until, now + 120)
        await self.guard.observe_error(RateLimitError("rate"))
        self.assertGreaterEqual((await self.store.get_scheduler_state()).until, state.until)
        await self.guard.observe_result({"status": "login_required", "reason": "http_429"})
        await self.guard.observe_response(response)
        await self.store.initialize()
        self.assertEqual((await self.store.get_scheduler_state()).state, "paused")
        await self.store.resume_scheduler()
        self.assertEqual((await self.store.get_scheduler_state()).state, "normal")

    async def test_business_limit_uses_retry_after_from_http_200(self):
        task_id = await self.store.create_sku_task("offline")
        control = self.guard.bind(task_id, ExecutionBudget(10))
        response = SimpleNamespace(status_code=200, headers={"Retry-After": "180"}, url="")
        now = time.time()
        await asyncio.to_thread(control.observe_response, response)
        await asyncio.to_thread(control.observe_error, RateLimitError("business rate limit"))
        self.assertGreaterEqual((await self.store.get_scheduler_state()).until, now + 180)

    async def test_observers_cover_error_result_and_redirect_without_reading_body(self):
        cases = [
            ("observe_error", AuthenticationError("expired"), "paused"),
            ("observe_error", RiskControlError("RATE_LIMIT CAPTCHA"), "paused"),
            ("observe_error", RateLimitError("rate"), "cooldown"),
            ("observe_result", {"status": "access_restricted", "reason": "http_429"}, "cooldown"),
            ("observe_result", {"status": "access_restricted", "reason": "challenge"}, "paused"),
            ("observe_result", {"ret": ["RATE_LIMIT", "CAPTCHA"]}, "paused"),
            ("observe_response", SimpleNamespace(status_code=302, headers={"Location": "https://login.1688.com/"}, url=""), "paused"),
            ("observe_response", SimpleNamespace(status_code=403, headers={}, url=""), "paused"),
        ]
        for method, value, expected in cases:
            with self.subTest(method=method, value=value):
                await self.store.resume_scheduler()
                await getattr(self.guard, method)(value)
                self.assertEqual((await self.store.get_scheduler_state()).state, expected)
        await self.store.resume_scheduler()
        await self.guard.observe_result({"status": "success", "skus": [{"title": "CAPTCHA"}]})
        await self.guard.observe_error(ValueError("ordinary parse error"))
        self.assertEqual((await self.store.get_scheduler_state()).state, "normal")

    async def test_http_date_and_invalid_retry_after(self):
        now = time.time()
        date = format_datetime(datetime.fromtimestamp(now + 180, timezone.utc), usegmt=True)
        response = SimpleNamespace(status_code=429, headers={"Retry-After": date}, url="")
        await self.guard.observe_response(response)
        self.assertGreater((await self.store.get_scheduler_state()).until, now + 178)
        for value in ("NaN", "inf", "-1", "1.5", "tomorrow", "9" * 1000, None):
            self.assertIsNone(retry_after_seconds(value, now))
        self.assertEqual(retry_after_seconds("0", now), 0)
        self.assertEqual(retry_after_seconds("120", now), 120)
