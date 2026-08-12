from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlencode

from image_search import ImageSearchClient, SearchOptions
from image_search.cookies import ImportedCookie
from image_search.errors import (
    AuthenticationError,
    ProtocolError,
    RateLimitError,
    RiskControlError,
    TokenError,
)

from .config import Settings
from .cookie_store import CookieStore, CookieStoreError
from .database import Database, TaskRecord
from .image_downloader import ImageDownloadError, download_image

LOGGER = logging.getLogger(__name__)


def build_search_page_url(image_id: str) -> str:
    query = urlencode(
        {
            "tab": "imageSearch",
            "imageId": image_id,
            "imageIdList": image_id,
            "spm": "a260k.home2025/2025.imagesearch.upload",
        }
    )
    return f"https://air.1688.com/kapp/1688-search/pc-image-search/?{query}"


def normalize_result(result: object) -> dict[str, object]:
    products = []
    for product in result.products:
        products.append(
            {
                "image": product.image_url,
                "title": product.title,
                "price": product.price,
                "sale_quantity": product.sale_quantity,
                "product_url": product.link_url,
            }
        )
    return {
        "search_page_url": build_search_page_url(result.upload.image_id),
        "image_id": result.upload.image_id,
        "found": result.found,
        "products": products,
    }


def classify_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ImageDownloadError):
        return exc.code, str(exc)
    if isinstance(exc, CookieStoreError):
        return "COOKIE_UNAVAILABLE", str(exc)
    if isinstance(exc, AuthenticationError):
        return "COOKIE_INVALID", str(exc)
    if isinstance(exc, TokenError):
        return "MTOP_TOKEN_INVALID", str(exc)
    if isinstance(exc, RiskControlError):
        return "RISK_CONTROL", str(exc)
    if isinstance(exc, RateLimitError):
        return "RATE_LIMITED", str(exc)
    if isinstance(exc, ProtocolError):
        return "PROTOCOL_ERROR", str(exc)
    if isinstance(exc, TimeoutError):
        return "TASK_TIMEOUT", "搜索任务执行超时"
    return "INTERNAL_ERROR", type(exc).__name__


class WorkerPool:
    def __init__(
        self,
        database: Database,
        cookie_store: CookieStore,
        settings: Settings,
    ) -> None:
        self.database = database
        self.cookie_store = cookie_store
        self.settings = settings
        self._workers: list[asyncio.Task[None]] = []
        self._cleanup_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.worker_count,
            thread_name_prefix="1688-search",
        )

    async def start(self) -> None:
        self._stop.clear()
        self._workers = [
            asyncio.create_task(self._worker_loop(index), name=f"search-worker-{index}")
            for index in range(self.settings.worker_count)
        ]
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="task-cleanup")

    async def stop(self) -> None:
        self._stop.set()
        tasks = [*self._workers]
        if self._cleanup_task is not None:
            tasks.append(self._cleanup_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._cleanup_task = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _worker_loop(self, worker_index: int) -> None:
        while not self._stop.is_set():
            task = await self.database.claim_task()
            if task is None:
                await asyncio.sleep(0.25)
                continue
            LOGGER.info("Worker %d 开始任务 %s", worker_index, task.task_id)
            try:
                result = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=self.settings.task_timeout_seconds,
                )
                await self.database.complete_task(task.task_id, result)
                LOGGER.info("Worker %d 完成任务 %s", worker_index, task.task_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code, message = classify_error(exc)
                await self.database.fail_task(task.task_id, code, message)
                LOGGER.warning("Worker %d 任务 %s 失败: %s", worker_index, task.task_id, code)

    async def _execute_task(self, task: TaskRecord) -> dict[str, object]:
        if task.cookie_version is None:
            raise CookieStoreError("任务领取时没有可用 Cookie")
        _version, records = await self.cookie_store.load_version(task.cookie_version)
        image_content = await download_image(
            task.image_url,
            max_bytes=self.settings.max_image_bytes,
            connect_timeout=self.settings.download_connect_timeout,
            total_timeout=self.settings.download_timeout,
        )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(self._search_sync, records, image_content),
        )

    def _search_sync(
        self,
        records: list[ImportedCookie],
        image_content: bytes,
    ) -> dict[str, object]:
        options = SearchOptions(
            result_limit=3,
            ready_retries=self.settings.search_ready_retries,
            request_interval=self.settings.search_ready_interval,
            max_image_bytes=self.settings.max_image_bytes,
        )
        client = ImageSearchClient(
            cookie_records=records,
            options=options,
            timeout=self.settings.search_http_timeout,
            network_retries=self.settings.search_network_retries,
        )
        return normalize_result(client.search_bytes(image_content))

    async def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(3600)
            cutoff = time.time() - self.settings.task_retention_seconds
            deleted = await self.database.cleanup_tasks(cutoff)
            if deleted:
                LOGGER.info("清理 %d 个过期任务", deleted)
