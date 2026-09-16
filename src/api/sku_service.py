"""Fail-fast admission; a slot belongs to the operation, not its HTTP waiter."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from image_search.cookies import ImportedCookie
from product_sku.client import ProductSkuClient
from product_sku.models import SkuResult

from .config import Settings
from .cookie_store import CookieStore, CookieStoreError


class SkuServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _unavailable() -> SkuServiceError:
    return SkuServiceError(503, "COOKIE_UNAVAILABLE", "服务器没有可用的 1688 Cookie")


class ProductSkuService:
    def __init__(self, cookie_store: CookieStore, settings: Settings):
        self.cookie_store = cookie_store
        self.settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=settings.sku_max_concurrency, thread_name_prefix="product-sku",
        )
        self._jobs: set[asyncio.Task[SkuResult]] = set()
        self._closing = False

    def _fetch(self, url: str, records: list[ImportedCookie]) -> SkuResult:
        try:
            client = ProductSkuClient(
                cookie_records=records, timeout=self.settings.sku_http_timeout,
                network_retries=1,
            )
        except ValueError:
            raise _unavailable() from None
        with client:
            return client.fetch(url)

    async def _run(self, url: str) -> SkuResult:
        try:
            _version, records = await self.cookie_store.load_active()
            return await asyncio.get_running_loop().run_in_executor(
                self._executor, self._fetch, url, records,
            )
        except CookieStoreError:
            raise _unavailable() from None
        except SkuServiceError:
            raise
        except Exception:
            # Never expose network exceptions, Cookie values or upstream HTML.
            raise SkuServiceError(502, "SKU_QUERY_FAILED", "SKU 查询失败") from None

    def _finished(self, job: asyncio.Task[SkuResult]) -> None:
        self._jobs.discard(job)
        # Consume detached failures after an HTTP timeout/disconnection.
        if not job.cancelled():
            job.exception()

    async def query(self, url: str) -> SkuResult:
        # This block contains no await: admission is atomic on the app event loop.
        if self._closing:
            raise SkuServiceError(503, "SKU_SERVICE_STOPPING", "SKU 服务正在停止")
        if len(self._jobs) >= self.settings.sku_max_concurrency:
            raise SkuServiceError(429, "SKU_BUSY", "SKU 查询并发已满，请稍后重试")
        job = asyncio.create_task(self._run(url))
        self._jobs.add(job)
        job.add_done_callback(self._finished)
        try:
            # A cancelled/timed-out waiter must NOT cancel the operation or free
            # its slot while the synchronous curl request is still running.
            return await asyncio.wait_for(
                asyncio.shield(job), timeout=self.settings.sku_query_timeout_seconds,
            )
        except TimeoutError:
            raise SkuServiceError(504, "SKU_QUERY_TIMEOUT", "SKU 查询等待超时") from None

    async def stop(self) -> None:
        self._closing = True
        if self._jobs:
            await asyncio.gather(
                *(asyncio.shield(job) for job in tuple(self._jobs)),
                return_exceptions=True,
            )
        # All accepted operations and their owned sessions have finished.
        self._executor.shutdown(wait=True)
