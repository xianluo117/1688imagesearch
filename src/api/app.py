from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from image_search.errors import AuthenticationError, ProtocolError

from .auth import require_api_key
from .config import Settings
from .cookie_store import CookieStore
from .database import Database, TaskRecord
from .schemas import (
    CookieUploadResponse,
    DetailedHealthResponse,
    HealthResponse,
    SearchTaskCreate,
    TaskCreated,
    TaskStatusResponse,
)
from .worker import WorkerPool


def _task_response(task: TaskRecord) -> TaskStatusResponse:
    error = None
    if task.status == "failed":
        error = {
            "code": task.error_code or "UNKNOWN_ERROR",
            "message": task.error_message or "任务执行失败",
        }
    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        result=task.result,
        error=error,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    cookie_store = CookieStore(database, resolved.cookie_encryption_key)
    workers = WorkerPool(database, cookie_store, resolved)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        await workers.start()
        try:
            yield
        finally:
            await workers.stop()

    app = FastAPI(
        title="1688 Image Search API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.database = database
    app.state.cookie_store = cookie_store
    app.state.workers = workers

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
            worker_limit=resolved.worker_count,
            cookie=await cookie_store.status(),
            tasks=await database.task_counts(),
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
        "/api/v1/search-tasks",
        response_model=TaskCreated,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_search_task(
        body: SearchTaskCreate,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> TaskCreated:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        if (await cookie_store.status())["available"] is not True:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "COOKIE_UNAVAILABLE", "message": "尚未上传 1688 Cookie"},
            )
        task_id = await database.create_task(
            str(body.image_url),
            max_queued=resolved.max_queued_tasks,
        )
        if task_id is None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "QUEUE_FULL", "message": "任务队列已满"},
            )
        return TaskCreated(task_id=task_id, status="queued")

    @app.get("/api/v1/search-tasks/{task_id}", response_model=TaskStatusResponse)
    async def get_search_task(
        task_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> TaskStatusResponse:
        require_api_key(x_api_key, resolved.search_api_key, scope="搜索")
        task = await database.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "TASK_NOT_FOUND", "message": "任务不存在"},
            )
        return _task_response(task)

    return app
