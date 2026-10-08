"""Persistent SKU admission and HTTP waiting; execution belongs to the scheduler."""
from __future__ import annotations

from pydantic import TypeAdapter

from product_sku.models import SkuResult

from .cookie_store import CookieStoreError
from .queue_store import IdempotencyConflict, QueueNotAccepting

_RESULT = TypeAdapter(SkuResult)


def restore_result(payload: dict) -> SkuResult:
    """Restore raw persisted data without applying HTTP price presentation."""
    return _RESULT.validate_python(payload)


def task_location(task_id: str) -> str:
    return f"/api/v2/sku-tasks/{task_id}"


class SkuServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, task_id=None):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message
        self.task_id = task_id

    def detail(self):
        detail = {"code": self.code, "message": self.message}
        if self.task_id is not None:
            detail.update(task_id=self.task_id, query_url=task_location(self.task_id))
        return detail


class ProductSkuService:
    def __init__(self, cookie_store, settings, scheduler):
        self.cookie_store, self.settings, self.scheduler = cookie_store, settings, scheduler
        self.database = scheduler.database

    async def submit(self, url: str, *, idempotency_key: str | None = None) -> str:
        if not self.database.accepting:
            raise SkuServiceError(503, "SKU_SERVICE_STOPPING", "SKU 服务正在停止")
        try:
            await self.cookie_store.load_active()
        except CookieStoreError:
            raise SkuServiceError(503, "COOKIE_UNAVAILABLE", "服务器没有可用的 1688 Cookie") from None
        try:
            task_id = await self.database.create_sku_task(url, idempotency_key=idempotency_key)
        except IdempotencyConflict:
            raise SkuServiceError(409, "IDEMPOTENCY_CONFLICT", "幂等键已用于不同参数") from None
        except QueueNotAccepting:
            raise SkuServiceError(503, "SKU_SERVICE_STOPPING", "SKU 服务正在停止") from None
        if task_id is None:
            raise SkuServiceError(429, "QUEUE_FULL", "任务队列已满")
        return task_id

    async def query(self, url: str, *, idempotency_key: str | None = None) -> SkuResult:
        task_id = await self.submit(url, idempotency_key=idempotency_key)
        try:
            task = await self.scheduler.wait_for_task(
                task_id, timeout=self.settings.sku_query_timeout_seconds,
            )
        except TimeoutError:
            raise SkuServiceError(504, "SKU_QUERY_TIMEOUT", "SKU 查询等待超时", task_id=task_id) from None
        if task is not None and task.result is not None:
            return restore_result(task.result)
        code = task.error_code if task else "SKU_QUERY_FAILED"
        status = {"COOKIE_UNAVAILABLE": 503, "TASK_TIMEOUT": 504,
                  "TASK_CANCELLED": 409, "SCHEDULER_STOPPING": 503}.get(code, 502)
        raise SkuServiceError(status, code if status != 502 else "SKU_QUERY_FAILED",
                              "SKU 查询失败", task_id=task_id)
