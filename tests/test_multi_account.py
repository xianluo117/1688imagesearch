from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from api.database import Database


class MultiAccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / "accounts.db")
        await self.db.initialize()
        await self.db.ensure_account("account-a", "账号 A")
        await self.db.ensure_account("account-b", "账号 B")
        await self.db.store_cookie(b"a", cookie_count=1, exported_at=None, account_id="account-a")
        await self.db.store_cookie(b"b", cookie_count=1, exported_at=None, account_id="account-b")

    async def test_round_robin_account_selection(self):
        selected = [await self.db.select_account() for _ in range(4)]
        self.assertEqual(selected, ["account-a", "account-b", "account-a", "account-b"])

    async def test_cookie_versions_are_isolated_per_account(self):
        a = await self.db.get_active_cookie("account-a")
        b = await self.db.get_active_cookie("account-b")
        self.assertEqual(a[1], b"a")
        self.assertEqual(b[1], b"b")
        self.assertNotEqual(a[0], b[0])

    async def test_sku_task_binds_selected_cookie_version(self):
        task_id = await self.db.create_sku_task("sku", account_id="account-a")
        task = await self.db.get_queue_task(task_id)
        self.assertEqual(task.account_id, "account-a")
        self.assertEqual(task.payload["cookie_version"], (await self.db.get_active_cookie("account-a"))[0])


if __name__ == "__main__":
    unittest.main()
