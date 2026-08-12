from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import aiosqlite
import httpx
from cryptography.fernet import Fernet

from api.app import create_app
from api.config import ConfigurationError, Settings
from api.cookie_store import CookieStore
from api.database import Database
from api.image_downloader import UnsafeImageUrlError, validate_public_url
from api.worker import build_search_page_url


UPLOAD_KEY = "upload-key-abcdefghijklmnopqrstuvwxyz"
SEARCH_KEY = "search-key-abcdefghijklmnopqrstuvwxyz"


def cookie_payload() -> dict[str, object]:
    return {
        "exportedAt": "2026-08-12T00:00:00Z",
        "cookies": [
            {"name": "cookie1", "value": "login", "domain": ".1688.com", "path": "/"},
            {"name": "_m_h5_tk", "value": "token_9999999999999", "domain": ".1688.com", "path": "/"},
            {"name": "_m_h5_tk_enc", "value": "enc", "domain": ".1688.com", "path": "/"},
        ],
    }


class DatabaseAndCookieTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "test.db")
        await self.database.initialize()
        self.key = Fernet.generate_key().decode("ascii")
        self.store = CookieStore(self.database, self.key)

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_cookie_is_encrypted_and_loadable(self) -> None:
        metadata = await self.store.save(cookie_payload())
        self.assertEqual(metadata["cookie_count"], 3)
        async with aiosqlite.connect(self.database.path) as db:
            cursor = await db.execute("SELECT encrypted_payload FROM cookie_versions")
            encrypted = bytes((await cursor.fetchone())[0])
        self.assertNotIn(b"token_9999999999999", encrypted)
        version, records = await self.store.load_active()
        self.assertEqual(version, 1)
        self.assertIn("cookie1", {record.name for record in records})

    async def test_task_state_machine(self) -> None:
        await self.store.save(cookie_payload())
        task_id = await self.database.create_task("https://example.com/a.jpg", max_queued=5)
        self.assertIsNotNone(task_id)
        claimed = await self.database.claim_task()
        self.assertEqual(claimed.task_id, task_id)
        self.assertEqual(claimed.status, "running")
        await self.database.complete_task(task_id, {"products": []})
        completed = await self.database.get_task(task_id)
        self.assertEqual(completed.status, "succeeded")


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_reject_loopback_and_private_urls(self) -> None:
        for url in ("http://127.0.0.1/a.jpg", "http://10.0.0.1/a.jpg", "http://[::1]/a.jpg"):
            with self.assertRaises(UnsafeImageUrlError):
                await validate_public_url(url)

    def test_search_page_url(self) -> None:
        url = build_search_page_url("123456")
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["imageId"], ["123456"])
        self.assertEqual(query["imageIdList"], ["123456"])
        self.assertEqual(query["tab"], ["imageSearch"])


class SettingsTests(unittest.TestCase):
    def test_worker_limit_and_distinct_keys(self) -> None:
        common = {
            "cookie_encryption_key": Fernet.generate_key().decode("ascii"),
            "database_path": Path("test.db"),
        }
        with self.assertRaises(ConfigurationError):
            Settings(
                cookie_upload_api_key=UPLOAD_KEY,
                search_api_key=SEARCH_KEY,
                worker_count=5,
                **common,
            )
        with self.assertRaises(ConfigurationError):
            Settings(
                cookie_upload_api_key=UPLOAD_KEY,
                search_api_key=UPLOAD_KEY,
                worker_count=4,
                **common,
            )


class ApiKeyIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            cookie_upload_api_key=UPLOAD_KEY,
            search_api_key=SEARCH_KEY,
            cookie_encryption_key=Fernet.generate_key().decode("ascii"),
            database_path=Path(self.temp.name) / "api.db",
            worker_count=1,
            max_queued_tasks=10,
        )
        self.app = create_app(self.settings)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.temp.cleanup()

    async def test_dual_api_key_isolation_and_task_creation(self) -> None:
        denied_upload = await self.client.post(
            "/api/v1/cookies",
            headers={"X-API-Key": SEARCH_KEY},
            json=cookie_payload(),
        )
        self.assertEqual(denied_upload.status_code, 401)

        uploaded = await self.client.post(
            "/api/v1/cookies",
            headers={"X-API-Key": UPLOAD_KEY},
            json=cookie_payload(),
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)

        denied_search = await self.client.post(
            "/api/v1/search-tasks",
            headers={"X-API-Key": UPLOAD_KEY},
            json={"image_url": "https://example.com/a.jpg"},
        )
        self.assertEqual(denied_search.status_code, 401)

        created = await self.client.post(
            "/api/v1/search-tasks",
            headers={"X-API-Key": SEARCH_KEY},
            json={"image_url": "https://example.com/a.jpg"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        task_id = created.json()["task_id"]
        fetched = await self.client.get(
            f"/api/v1/search-tasks/{task_id}",
            headers={"X-API-Key": SEARCH_KEY},
        )
        self.assertIn(fetched.status_code, {200})
        self.assertIn(fetched.json()["status"], {"queued", "running", "failed"})


if __name__ == "__main__":
    unittest.main()
