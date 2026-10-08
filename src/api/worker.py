"""Business execution helpers. Production consumption lives in queue_scheduler."""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlencode

from image_search import ImageSearchClient, SearchOptions
from image_search.errors import (
    AuthenticationError, MtopNetworkError, ProductsNotFoundError, ProtocolError,
    RateLimitError, RiskControlError, TaskCancelledError, TokenError, UploadNoImageIdError,
)
from product_sku.client import ProductSkuClient

from .cookie_store import CookieStoreError
from .image_downloader import ImageDownloadError, download_image

LOGGER = logging.getLogger(__name__)


def build_search_page_url(image_id: str) -> str:
    query = urlencode({"tab": "imageSearch", "imageId": image_id, "imageIdList": image_id,
                       "spm": "a260k.home2025/2025.imagesearch.upload"})
    return f"https://air.1688.com/kapp/1688-search/pc-image-search/?{query}"


def normalize_product_result(result: object, *, search_page_url: str) -> dict[str, object]:
    products = [{"image": p.image_url, "title": p.title, "price": p.price,
                 "sale_quantity": p.sale_quantity, "product_url": p.link_url}
                for p in result.products[:3]]
    if not products:
        raise ProductsNotFoundError("图片搜索未返回商品")
    return {"search_page_url": search_page_url, "image_id": result.upload.image_id,
            "found": result.found, "products": products}


def classify_error(exc: Exception, *, task_kind: str) -> tuple[str, str]:
    if isinstance(exc, ImageDownloadError):
        return exc.code, str(exc)
    for cls, code in (
        (TaskCancelledError, "TASK_CANCELLED"), (CookieStoreError, "COOKIE_UNAVAILABLE"),
        (AuthenticationError, "COOKIE_INVALID"), (TokenError, "MTOP_TOKEN_INVALID"),
        (RiskControlError, "RISK_CONTROL"), (RateLimitError, "RATE_LIMITED"),
        (MtopNetworkError, "MTOP_NETWORK_ERROR"), (UploadNoImageIdError, "UPLOAD_NO_IMAGE_ID"),
        (ProductsNotFoundError, "PRODUCTS_NOT_FOUND"), (TimeoutError, "TASK_TIMEOUT"),
        (ProtocolError, "PROTOCOL_ERROR"),
    ):
        if isinstance(exc, cls):
            return code, f"{task_kind}任务执行超时" if code == "TASK_TIMEOUT" else str(exc)
    return "INTERNAL_ERROR", type(exc).__name__


class QueueTaskExecutor:
    """Prepare asynchronously, run all synchronous clients on the scheduler's pool."""

    def __init__(self, database, cookie_store, settings):
        self.database, self.cookie_store, self.settings = database, cookie_store, settings

    async def execute(self, task, control, run_sync, execute_sku_sync):
        if task.kind == "sku":
            _, records = await self.cookie_store.load_active()
            return await run_sync(execute_sku_sync, task.payload["product_url"], records, control)
        if task.kind == "upload":
            upload = await self.database.get_upload_task(task.task_id)
            if upload is None or upload.cookie_version is None:
                raise CookieStoreError("任务领取时没有可用 Cookie")
            _, records = await self.cookie_store.load_version(upload.cookie_version)
            # External download belongs to the budget, but never consumes a permit.
            content = await download_image(
                task.payload["image_url"], max_bytes=self.settings.max_image_bytes,
                connect_timeout=self.settings.download_connect_timeout,
                total_timeout=self.settings.download_timeout,
            )
            return await run_sync(self.upload_sync, records, content, control)
        product = await self.database.get_product_task(task.task_id)
        upload = await self.database.get_upload_task(task.dependency_id)
        if product is None or upload is None or upload.status != "succeeded" or not upload.image_id or not upload.search_page_url:
            raise ProtocolError("关联上传任务结果不可用")
        _, records = await self.cookie_store.load_version(product.cookie_version)
        return await run_sync(self.product_sync, records, upload.image_id, upload.search_page_url, control)

    def _image_client(self, records, control):
        return ImageSearchClient(
            cookie_records=records,
            options=SearchOptions(result_limit=3, ready_retries=self.settings.search_ready_retries,
                                  request_interval=self.settings.search_ready_interval,
                                  max_image_bytes=self.settings.max_image_bytes),
            timeout=self.settings.search_http_timeout,
            network_retries=self.settings.search_network_retries, request_control=control,
        )

    def upload_sync(self, records, content, control):
        client = self._image_client(records, control)
        try:
            image_id = client.upload_image_bytes(content).image_id
            return {"image_id": image_id, "search_page_url": build_search_page_url(image_id)}
        finally:
            client.session.close()

    def product_sync(self, records, image_id, search_page_url, control):
        client = self._image_client(records, control)
        try:
            result = client.search_image_id(image_id)
            return normalize_product_result(result, search_page_url=search_page_url)
        finally:
            client.session.close()

    def sku_sync(self, url, records, control):
        try:
            client = ProductSkuClient(cookie_records=records, timeout=self.settings.sku_http_timeout,
                                      network_retries=1, request_control=control)
        except ValueError:
            raise CookieStoreError("服务器没有可用的 1688 Cookie") from None
        with client as active_client:
            return active_client.fetch(url).to_dict()


class _RetiredWorkerPool:
    """Import compatibility only; fail closed until the API uses the unique scheduler."""

    def __init__(self, database, cookie_store, settings):
        self.database, self.cookie_store, self.settings = database, cookie_store, settings

    async def start(self):
        raise RuntimeError("旧分类型工作池已停用；请在应用生命周期仅启动 UnifiedQueueScheduler")

    async def stop(self):
        pass


class UploadWorkerPool(_RetiredWorkerPool):
    pass


class ProductWorkerPool(_RetiredWorkerPool):
    pass


class TaskCleanupService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self._task = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._cleanup_loop(), name="task-cleanup")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            deleted = await self.database.cleanup_queue_tasks(time.time() - self.settings.task_retention_seconds)
            if deleted:
                LOGGER.info("清理 %d 个过期任务", deleted)
