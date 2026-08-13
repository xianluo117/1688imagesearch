from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from image_search.errors import AuthenticationError, ProtocolError

from .auth import require_api_key
from .config import Settings
from .cookie_store import CookieStore
from .database import Database, ProductTaskRecord, UploadTaskRecord
from .schemas import (
    CookieUploadResponse,
    DetailedHealthResponse,
    HealthResponse,
    ProductTaskCreate,
    ProductTaskCreated,
    ProductTaskStatusResponse,
    UploadTaskCreate,
    UploadTaskCreated,
    UploadTaskStatusResponse,
)
from .worker import ProductWorkerPool, TaskCleanupService, UploadWorkerPool


def _error_payload(code: str | None, message: str | None) -> dict[str, str]:
    return {
        "code": code or "UNKNOWN_ERROR",
        "message": message or "任务执行失败",
    }


def _upload_task_response(task: UploadTaskRecord) -> UploadTaskStatusResponse:
    result = None
    if task.status == "succeeded" and task.image_id and task.search_page_url:
        result = {"image_id": task.image_id, "search_page_url": task.search_page_url}
    error = _error_payload(task.error_code, task.error_message) if task.status in {"failed", "cancelled"} else None
    return UploadTaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result=result,
        error=error,
    )


def _product_task_response(task: ProductTaskRecord) -> ProductTaskStatusResponse:
    error = _error_payload(task.error_code, task.error_message) if task.status in {"failed", "cancelled"} else None
    return ProductTaskStatusResponse(
        task_id=task.task_id,
        upload_task_id=task.upload_task_id,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result=task.result,
        error=error,
    )


def _raise_product_creation_error(code: str) -> None:
    definitions = {
        "UPLOAD_TASK_NOT_FOUND": (status.HTTP_404_NOT_FOUND, "上传任务不存在"),
        "UPLOAD_TASK_NOT_READY": (status.HTTP_409_CONFLICT, "上传任务尚未成功"),
        "UPLOAD_TASK_CANCELLED": (status.HTTP_409_CONFLICT, "上传任务已取消"),
        "UPLOAD_TASK_FAILED": (status.HTTP_409_CONFLICT, "上传任务已失败"),
        "QUEUE_FULL": (status.HTTP_429_TOO_MANY_REQUESTS, "任务队列已满"),
    }
    status_code, message = definitions[code]
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    cookie_store = CookieStore(database, resolved.cookie_encryption_key)
    upload_workers = UploadWorkerPool(database, cookie_store, resolved)
    product_workers = ProductWorkerPool(database, cookie_store, resolved)
    cleanup_service = TaskCleanupService(database, resolved)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        await upload_workers.start()
        await product_workers.start()
        await cleanup_service.start()
        try:
            yield
        finally:
            await cleanup_service.stop()
            await product_workers.stop()
            await upload_workers.stop()

    app = FastAPI(
        title="1688 Two-stage Image Search API",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.database = database
    app.state.cookie_store = cookie_store
    app.state.upload_workers = upload_workers
    app.state.product_workers = product_workers

    @app.exception_handler(ProtocolError)
    async def protocol_error_handler(_request: Request, exc: ProtocolError) -> JSONResponse:
        code = "COOKIE_INVALID" if isinstance(exc, AuthenticationError) else "INVALID_REQUEST"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": {"code": code, "message": str(exc)}},
        )

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/api/v1/health", response_model=DetailedHealthResponse)
    async def detailed_health(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> DetailedHealthResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        return DetailedHealthResponse(
            status="ok",
            cookie=await cookie_store.status(),
            upload_workers=resolved.upload_worker_count,
            product_workers=resolved.product_worker_count,
            upload_tasks=await database.upload_task_counts(),
            product_tasks=await database.product_task_counts(),
        )

    @app.post("/api/v1/cookies", response_model=CookieUploadResponse)
    async def upload_cookies(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> CookieUploadResponse:
        require_api_key(x_api_key, resolved.cookie_upload_api_key, scope="Cookie 上传")
        try:
            payload: Any = await request.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_JSON", "message": "请求体不是有效 JSON"},
            ) from exc
        metadata = await cookie_store.save(payload)
        return CookieUploadResponse(status="updated", **metadata)

    @app.post(
        "/api/v1/upload-tasks",
        response_model=UploadTaskCreated,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_upload_task(
        body: UploadTaskCreate,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> UploadTaskCreated:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        if (await cookie_store.status())["available"] is not True:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "COOKIE_UNAVAILABLE", "message": "尚未上传 1688 Cookie"},
            )
        task_id = await database.create_upload_task(
            str(body.image_url),
            max_active=resolved.max_queued_tasks,
        )
        if task_id is None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUEUE_FULL", "message": "任务队列已满"},
            )
        return UploadTaskCreated(task_id=task_id, status="queued")

    @app.get("/api/v1/upload-tasks/{task_id}", response_model=UploadTaskStatusResponse)
    async def get_upload_task(
        task_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> UploadTaskStatusResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task = await database.get_upload_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "UPLOAD_TASK_NOT_FOUND", "message": "上传任务不存在"},
            )
        return _upload_task_response(task)

    @app.delete("/api/v1/upload-tasks/{task_id}", response_model=UploadTaskStatusResponse)
    async def cancel_upload_task(
        task_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> UploadTaskStatusResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task = await database.cancel_upload_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "UPLOAD_TASK_NOT_FOUND", "message": "上传任务不存在"},
            )
        return _upload_task_response(task)

    @app.post(
        "/api/v1/product-tasks",
        response_model=ProductTaskCreated,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_product_task(
        body: ProductTaskCreate,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> ProductTaskCreated:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task_id, error_code = await database.create_product_task(
            body.upload_task_id,
            max_active=resolved.max_queued_tasks,
        )
        if error_code is not None:
            _raise_product_creation_error(error_code)
        assert task_id is not None
        return ProductTaskCreated(
            task_id=task_id,
            upload_task_id=body.upload_task_id,
            status="queued",
        )

    @app.get("/api/v1/product-tasks/{task_id}", response_model=ProductTaskStatusResponse)
    async def get_product_task(
        task_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> ProductTaskStatusResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task = await database.get_product_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PRODUCT_TASK_NOT_FOUND", "message": "商品任务不存在"},
            )
        return _product_task_response(task)

    @app.delete("/api/v1/product-tasks/{task_id}", response_model=ProductTaskStatusResponse)
    async def cancel_product_task(
        task_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> ProductTaskStatusResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task = await database.cancel_product_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PRODUCT_TASK_NOT_FOUND", "message": "商品任务不存在"},
            )
        return _product_task_response(task)

    return app
