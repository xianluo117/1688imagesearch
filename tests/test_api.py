from __future__ import annotations

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

    async def test_schema_b_migration_drops_legacy_table(self) -> None:
        async with aiosqlite.connect(self.database.path) as db:
            await db.execute("CREATE TABLE search_tasks(task_id TEXT PRIMARY KEY)")
            await db.execute("INSERT INTO search_tasks VALUES ('legacy')")
            await db.commit()
        await self.database.initialize()
        async with aiosqlite.connect(self.database.path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='search_tasks'"
            )
            self.assertIsNone(await cursor.fetchone())

    async def test_upload_and_product_state_machines(self) -> None:
        await self.store.save(cookie_payload())
        upload_id = await self.database.create_upload_task("https://example.com/a.jpg", max_active=5)
        self.assertIsNotNone(upload_id)
        upload = await self.database.claim_upload_task()
        self.assertEqual(upload.task_id, upload_id)
        await self.database.complete_upload_task(
            upload_id,
            image_id="image-1",
            search_page_url="https://air.1688.com/search?imageId=image-1",
        )
        product_id, error = await self.database.create_product_task(upload_id, max_active=5)
        self.assertIsNone(error)
        self.assertIsNotNone(product_id)
        product = await self.database.claim_product_task()
        self.assertEqual(product.task_id, product_id)
        await self.database.complete_product_task(
            product_id,
            {"image_id": "image-1", "search_page_url": "url", "found": 1, "products": []},
        )
        self.assertEqual((await self.database.get_product_task(product_id)).status, "succeeded")

    async def test_duplicate_product_tasks_get_new_ids(self) -> None:
        await self.store.save(cookie_payload())
        upload_id = await self.database.create_upload_task("https://example.com/a.jpg", max_active=5)
        await self.database.claim_upload_task()
        await self.database.complete_upload_task(upload_id, image_id="image-1", search_page_url="url")
        first, first_error = await self.database.create_product_task(upload_id, max_active=5)
        second, second_error = await self.database.create_product_task(upload_id, max_active=5)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertNotEqual(first, second)

    async def test_queued_cancel_and_product_creation_rejection(self) -> None:
        await self.store.save(cookie_payload())
        upload_id = await self.database.create_upload_task("https://example.com/a.jpg", max_active=5)
        cancelled = await self.database.cancel_upload_task(upload_id)
        self.assertEqual(cancelled.status, "cancelled")
        product_id, error = await self.database.create_product_task(upload_id, max_active=5)
        self.assertIsNone(product_id)
        self.assertEqual(error, "UPLOAD_TASK_CANCELLED")

    async def test_restart_recovery(self) -> None:
        await self.store.save(cookie_payload())
        upload_id = await self.database.create_upload_task("https://example.com/a.jpg", max_active=5)
        await self.database.claim_upload_task()
        async with aiosqlite.connect(self.database.path) as db:
            await db.execute(
                "UPDATE upload_tasks SET cancel_requested=1 WHERE task_id=?",
                (upload_id,),
            )
            await db.commit()
        await self.database.initialize()
        self.assertEqual((await self.database.get_upload_task(upload_id)).status, "cancelled")


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
    def test_worker_limits_and_distinct_keys(self) -> None:
        common = {
            "cookie_encryption_key": Fernet.generate_key().decode("ascii"),
            "database_path": Path("test.db"),
        }
        with self.assertRaises(ConfigurationError):
            Settings(
                cookie_upload_api_key=UPLOAD_KEY,
                search_api_key=SEARCH_KEY,
                upload_worker_count=3,
                **common,
            )
        with self.assertRaises(ConfigurationError):
            Settings(
                cookie_upload_api_key=UPLOAD_KEY,
                search_api_key=UPLOAD_KEY,
                **common,
            )


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            cookie_upload_api_key=UPLOAD_KEY,
            search_api_key=SEARCH_KEY,
            cookie_encryption_key=Fernet.generate_key().decode("ascii"),
            database_path=Path(self.temp.name) / "api.db",
            upload_worker_count=1,
            product_worker_count=1,
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

    async def test_two_stage_routes_and_key_isolation(self) -> None:
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
        denied_task = await self.client.post(
            "/api/v1/upload-tasks",
            headers={"X-API-Key": UPLOAD_KEY},
            json={"image_url": "https://example.com/a.jpg"},
        )
        self.assertEqual(denied_task.status_code, 401)
        created = await self.client.post(
            "/api/v1/upload-tasks",
            headers={"X-API-Key": SEARCH_KEY},
            json={"image_url": "https://example.com/a.jpg"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        task_id = created.json()["task_id"]
        fetched = await self.client.get(
            f"/api/v1/upload-tasks/{task_id}",
            headers={"X-API-Key": SEARCH_KEY},
        )
        self.assertEqual(fetched.status_code, 200)
        legacy = await self.client.post(
            "/api/v1/search-tasks",
            headers={"X-API-Key": SEARCH_KEY},
            json={"image_url": "https://example.com/a.jpg"},
        )
        self.assertEqual(legacy.status_code, 404)

    async def test_health_shape(self) -> None:
        response = await self.client.get(
            "/api/v1/health",
            headers={"X-API-Key": SEARCH_KEY},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["upload_workers"], 1)
        self.assertEqual(payload["product_workers"], 1)
        self.assertIn("cancelled", payload["upload_tasks"])
        self.assertIn("cancelled", payload["product_tasks"])


if __name__ == "__main__":
    unittest.main()
