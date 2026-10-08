"""Configuration-only regression; no network or environment file access."""
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from api.config import ConfigurationError, Settings


class QueueConfigTests(unittest.TestCase):
    def make(self, **kwargs):
        return Settings(cookie_upload_api_key="u" * 24, search_api_key="s" * 24,
                        cookie_encryption_key="offline", database_path=Path("unused"), **kwargs)

    def test_defaults_and_deprecated_counts_do_not_override_global(self):
        settings = self.make(upload_worker_count=2, product_worker_count=2, sku_max_concurrency=8)
        self.assertEqual(settings.global_worker_count, 1)
        self.assertEqual(settings.max_queued_tasks, 100)
        self.assertEqual((settings.request_interval_min_seconds, settings.request_interval_max_seconds), (2, 4))
        self.assertEqual(settings.rate_limit_cooldown_seconds, 60)
        self.assertEqual(settings.sku_task_timeout_seconds, 180)

    def test_integer_ranges(self):
        for name, limit in (("global_worker_count", 32), ("max_queued_tasks", 100000)):
            for value in (0, -1, True, 1.5, limit + 1):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                    self.make(**{name: value})
            self.make(**{name: limit})

    def test_finite_time_ranges_and_order(self):
        for name, upper in (("request_interval_min_seconds", 3600), ("request_interval_max_seconds", 3600),
                            ("rate_limit_cooldown_seconds", 86400), ("sku_task_timeout_seconds", 86400),
                            ("upload_task_timeout_seconds", 86400), ("product_task_timeout_seconds", 86400)):
            for value in (float("nan"), float("inf"), float("-inf"), 0, -1, True, "2", upper + 1):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                    self.make(**{name: value})
        with self.assertRaises(ConfigurationError):
            self.make(request_interval_min_seconds=5, request_interval_max_seconds=4)

    def test_environment_mapping_and_legacy_default_independence(self):
        env = {"COOKIE_UPLOAD_API_KEY": "u" * 24, "SEARCH_API_KEY": "s" * 24,
               "COOKIE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
               "UPLOAD_WORKER_COUNT": "2", "PRODUCT_WORKER_COUNT": "2", "SKU_MAX_CONCURRENCY": "8"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Settings.from_env().global_worker_count, 1)
            overrides = {"GLOBAL_WORKER_COUNT": "3", "REQUEST_INTERVAL_MIN_SECONDS": "1.5",
                         "REQUEST_INTERVAL_MAX_SECONDS": "3.5", "RATE_LIMIT_COOLDOWN_SECONDS": "120",
                         "SKU_TASK_TIMEOUT_SECONDS": "300", "MAX_QUEUED_TASKS": "200"}
            with patch.dict(os.environ, overrides):
                value = Settings.from_env()
                self.assertEqual(value.global_worker_count, 3)
                self.assertEqual(value.request_interval_min_seconds, 1.5)
                self.assertEqual(value.request_interval_max_seconds, 3.5)
                self.assertEqual(value.rate_limit_cooldown_seconds, 120)
                self.assertEqual(value.sku_task_timeout_seconds, 300)
                self.assertEqual(value.max_queued_tasks, 200)
            for name in ("REQUEST_INTERVAL_MIN_SECONDS", "SKU_TASK_TIMEOUT_SECONDS", "RATE_LIMIT_COOLDOWN_SECONDS"):
                with patch.dict(os.environ, {name: "NaN"}), self.assertRaises(ConfigurationError):
                    Settings.from_env()
