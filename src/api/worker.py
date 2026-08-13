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
    MtopNetworkError,
    ProductsNotFoundError,
    ProtocolError,
    RateLimitError,
    RiskControlError,
    TaskCancelledError,
    TokenError,
    UploadNoImageIdError,
)

from .config import Settings
from .cookie_store import CookieStore, CookieStoreError
from .database import Database, ProductTaskRecord, UploadTaskRecord
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


def normalize_product_result(
    result: object,
    *,
    search_page_url: str,
) -> dict[str, object]:
    products = [
        {
            "image": product.image_url,
            "title": product.title,
            "price": product.price,
            "sale_quantity": product.sale_quantity,
            "product_url": product.link_url,
        }
        for product in result.products[:3]
    ]
    if not products:
        raise ProductsNotFoundError("图片搜索未返回商品")
    return {
        "search_page_url": search_page_url,
        "image_id": result.upload.image_id,
        "found": result.found,
        "products": products,
    }


def classify_error(exc: Exception, *, task_kind: str) -> tuple[str, str]:
    if isinstance(exc, TaskCancelledError):
        return "TASK_CANCELLED", str(exc)
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
    if isinstance(exc, MtopNetworkError):
        return "MTOP_NETWORK_ERROR", str(exc)
    if isinstance(exc, UploadNoImageIdError):
        return "UPLOAD_NO_IMAGE_ID", str(exc)
    if isinstance(exc, ProductsNotFoundError):
        return "PRODUCTS_NOT_FOUND", str(exc)
    if isinstance(exc, TimeoutError):
        return "TASK_TIMEOUT", f"{task_kind}任务执行超时"
    if isinstance(exc, ProtocolError):
        return "PROTOCOL_ERROR", str(exc)
    return "INTERNAL_ERROR", type(exc).__name__


class UploadWorkerPool:
    def __init__(self, database: Database, cookie_store: CookieStore, settings: Settings) -> None:
        self.database = database
        self.cookie_store = cookie_store
        self.settings = settings
        self._workers: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.upload_worker_count,
            thread_name_prefix="1688-upload",
        )

    async def start(self) -> None:
        self._stop.clear()
        self._workers = [
            asyncio.create_task(self._worker_loop(index), name=f"upload-worker-{index}")
            for index in range(self.settings.upload_worker_count)
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _worker_loop(self, worker_index: int) -> None:
        while not self._stop.is_set():
            task = await self.database.claim_upload_task()
            if task is None:
                await asyncio.sleep(0.25)
                continue
            LOGGER.info("上传 Worker %d 开始任务 %s", worker_index, task.task_id)
            try:
                image_id = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=self.settings.upload_task_timeout_seconds,
                )
                final_status = await self.database.complete_upload_task(
                    task.task_id,
                    image_id=image_id,
                    search_page_url=build_search_page_url(image_id),
                )
                LOGGER.info("上传 Worker %d 结束任务 %s: status=%s", worker_index, task.task_id, final_status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code, message = classify_error(exc, task_kind="上传")
                final_status = await self.database.fail_upload_task(task.task_id, code, message)
                LOGGER.warning(
                    "上传 Worker %d 任务 %s 失败: status=%s code=%s message=%s",
                    worker_index,
                    task.task_id,
                    final_status,
                    code,
                    message,
                )

    async def _execute_task(self, task: UploadTaskRecord) -> str:
        await self._check_cancel(task.task_id)
        if task.cookie_version is None:
            raise CookieStoreError("任务领取时没有可用 Cookie")
        _version, records = await self.cookie_store.load_version(task.cookie_version)
        image_content = await download_image(
            task.image_url,
            max_bytes=self.settings.max_image_bytes,
            connect_timeout=self.settings.download_connect_timeout,
            total_timeout=self.settings.download_timeout,
        )
        await self._check_cancel(task.task_id)
        loop = asyncio.get_running_loop()
        image_id = await loop.run_in_executor(
            self._executor,
            partial(self._upload_sync, records, image_content),
        )
        await self._check_cancel(task.task_id)
        return image_id

    async def _check_cancel(self, task_id: str) -> None:
        if await self.database.is_upload_cancel_requested(task_id):
            raise TaskCancelledError("上传任务已取消")

    def _upload_sync(self, records: list[ImportedCookie], image_content: bytes) -> str:
        client = ImageSearchClient(
            cookie_records=records,
            options=SearchOptions(max_image_bytes=self.settings.max_image_bytes),
            timeout=self.settings.search_http_timeout,
            network_retries=self.settings.search_network_retries,
        )
        return client.upload_image_bytes(image_content).image_id


class ProductWorkerPool:
    def __init__(self, database: Database, cookie_store: CookieStore, settings: Settings) -> None:
        self.database = database
        self.cookie_store = cookie_store
        self.settings = settings
        self._workers: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.product_worker_count,
            thread_name_prefix="1688-product",
        )

    async def start(self) -> None:
        self._stop.clear()
        self._workers = [
            asyncio.create_task(self._worker_loop(index), name=f"product-worker-{index}")
            for index in range(self.settings.product_worker_count)
        ]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _worker_loop(self, worker_index: int) -> None:
        while not self._stop.is_set():
            task = await self.database.claim_product_task()
            if task is None:
                await asyncio.sleep(0.25)
                continue
            LOGGER.info("商品 Worker %d 开始任务 %s", worker_index, task.task_id)
            try:
                result = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=self.settings.product_task_timeout_seconds,
                )
                final_status = await self.database.complete_product_task(task.task_id, result)
                LOGGER.info("商品 Worker %d 结束任务 %s: status=%s", worker_index, task.task_id, final_status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code, message = classify_error(exc, task_kind="商品")
                final_status = await self.database.fail_product_task(task.task_id, code, message)
                LOGGER.warning(
                    "商品 Worker %d 任务 %s 失败: status=%s code=%s message=%s",
                    worker_index,
                    task.task_id,
                    final_status,
                    code,
                    message,
                )

    async def _execute_task(self, task: ProductTaskRecord) -> dict[str, object]:
        await self._check_cancel(task.task_id)
        upload = await self.database.get_upload_task(task.upload_task_id)
        if upload is None or upload.status != "succeeded" or not upload.image_id or not upload.search_page_url:
            raise ProtocolError("关联上传任务结果不可用")
        _version, records = await self.cookie_store.load_version(task.cookie_version)
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor,
            partial(
                self._search_sync,
                records,
                upload.image_id,
                upload.search_page_url,
                task.task_id,
                loop,
            ),
        )
        await self._check_cancel(task.task_id)
        return result

    async def _check_cancel(self, task_id: str) -> None:
        if await self.database.is_product_cancel_requested(task_id):
            raise TaskCancelledError("商品任务已取消")

    def _search_sync(
        self,
        records: list[ImportedCookie],
        image_id: str,
        search_page_url: str,
        task_id: str,
        loop: asyncio.AbstractEventLoop,
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

        def cancel_check() -> bool:
            future = asyncio.run_coroutine_threadsafe(
                self.database.is_product_cancel_requested(task_id),
                loop,
            )
            return future.result(timeout=5)

        result = client.search_image_id(image_id, cancel_check=cancel_check)
        return normalize_product_result(result, search_page_url=search_page_url)


class TaskCleanupService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._cleanup_loop(), name="task-cleanup")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _cleanup_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(3600)
            cutoff = time.time() - self.settings.task_retention_seconds
            deleted = await self.database.cleanup_tasks(cutoff)
            if deleted:
                LOGGER.info("清理 %d 个过期任务", deleted)
