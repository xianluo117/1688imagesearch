"""Offline start-pacing tests using a virtual monotonic clock."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from api.sku_rate_limit import QueryStartLimiter


class QueryStartLimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.sleeps = []
        self.clock = patch("api.sku_rate_limit.time.monotonic", side_effect=lambda: self.now)
        self.sleep = patch("api.sku_rate_limit.time.sleep", side_effect=self.advance)
        self.random = patch("api.sku_rate_limit.random.uniform", return_value=3.0)
        self.clock.start()
        self.sleep.start()
        self.uniform = self.random.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.sleep.stop)
        self.addCleanup(self.random.stop)
        self.limiter = QueryStartLimiter()

    def advance(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def test_first_start_immediate_and_random_range(self):
        self.limiter.wait()
        self.assertEqual(self.sleeps, [])
        self.uniform.assert_called_once_with(2.0, 4.0)

    def test_successive_starts_use_independent_intervals(self):
        self.uniform.side_effect = [2.0, 4.0, 3.0]
        starts = []
        for _ in range(3):
            self.limiter.wait()
            starts.append(self.now)
        self.assertEqual(starts, [100.0, 102.0, 106.0])
        self.assertEqual(self.sleeps, [2.0, 4.0])

    def test_elapsed_work_counts_towards_start_interval(self):
        self.limiter.wait()
        self.now += 1.0
        self.limiter.wait()
        self.assertEqual(self.sleeps, [2.0])

    def test_idle_period_does_not_allow_catch_up_burst(self):
        self.limiter.wait()
        self.now = 200.0
        self.limiter.wait()
        self.assertEqual(self.sleeps, [])
        self.limiter.wait()
        self.assertEqual(self.now, 203.0)
        self.assertEqual(self.sleeps, [3.0])

    def test_early_wakeup_rechecks_deadline(self):
        self.limiter.wait()
        calls = 0

        def early_sleep(seconds):
            nonlocal calls
            calls += 1
            self.now += seconds / 2 if calls == 1 else seconds

        with patch("api.sku_rate_limit.time.sleep", side_effect=early_sleep):
            self.limiter.wait()
        self.assertEqual(calls, 2)
        self.assertEqual(self.now, 103.0)

    def test_concurrent_workers_share_one_start_gate(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            futures = [workers.submit(self.limiter.wait) for _ in range(4)]
            for future in futures:
                future.result(timeout=2)
        self.assertEqual(self.sleeps, [3.0, 3.0, 3.0])
        self.assertEqual(self.now, 109.0)
        self.assertEqual(self.uniform.call_count, 4)


if __name__ == "__main__":
    unittest.main()
