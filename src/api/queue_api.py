"""Authenticated queue observability, operator recovery and asynchronous SKU API."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .schemas import TaskStatus
from .sku import _authorize
from .sku_http import canonical_url, idempotency_key, query_failed
from .sku_schemas import ProductSkuRequest
from .sku_service import SkuServiceError, restore_result, task_location
from .sku_v2_converter import convert_result
from .sku_v2_schemas import ProductSkuResponseV2

router = APIRouter(dependencies=[Depends(_authorize)])


class SchedulerStatus(BaseModel):
    state: str
    reason: str | None
    until: float | None
    updated_at: float
    cooldown_remaining: float


class SkuTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    cancel_requested: bool
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None
    query_url: str
    result: ProductSkuResponseV2 | None
    error: dict[str, str] | None
    scheduler: SchedulerStatus


async def scheduler_status(database) -> dict:
    return (await database.queue_snapshot())["scheduler"]


async def sku_task_response(task, database) -> SkuTaskResponse:
    try:
        result = convert_result(restore_result(task.result)) if task.result is not None else None
    except Exception:
        raise query_failed() from None
    return SkuTaskResponse(
        task_id=task.task_id, status=task.status, cancel_requested=task.cancel_requested,
        created_at=task.created_at, updated_at=task.updated_at, started_at=task.started_at,
        finished_at=task.finished_at, query_url=task_location(task.task_id), result=result,
        error={"code": task.error_code, "message": task.error_message or "任务执行失败"} if task.error_code else None,
        scheduler=await scheduler_status(database),
    )


async def get_sku_task(database, task_id):
    task = await database.get_queue_task(task_id)
    if task is None or task.kind != "sku":
        raise HTTPException(404, detail={"code": "SKU_TASK_NOT_FOUND", "message": "SKU 任务不存在"})
    return task


@router.post("/api/v2/sku-tasks", response_model=SkuTaskResponse, status_code=202)
async def create_sku_task(body: ProductSkuRequest, request: Request, response: Response):
    url, key = canonical_url(body.product_url), idempotency_key(request)
    try:
        task_id = await request.app.state.sku_service.submit(url, idempotency_key=key)
    except SkuServiceError as exc:
        raise HTTPException(exc.status_code, detail=exc.detail()) from None
    response.headers["Location"] = task_location(task_id)
    database = request.app.state.database
    return await sku_task_response(await get_sku_task(database, task_id), database)


@router.get("/api/v2/sku-tasks/{task_id}", response_model=SkuTaskResponse)
async def query_sku_task(task_id: str, request: Request):
    database = request.app.state.database
    return await sku_task_response(await get_sku_task(database, task_id), database)


@router.delete("/api/v2/sku-tasks/{task_id}", response_model=SkuTaskResponse)
async def cancel_sku_task(task_id: str, request: Request):
    database = request.app.state.database
    await get_sku_task(database, task_id)
    task = await database.cancel_queue_task(task_id)
    if task is None:
        raise HTTPException(404, detail={"code": "SKU_TASK_NOT_FOUND", "message": "SKU 任务不存在"})
    return await sku_task_response(task, database)


@router.get("/api/v1/queue")
async def queue_status(request: Request):
    snapshot = await request.app.state.scheduler.snapshot()
    snapshot["global_workers"] = request.app.state.settings.global_worker_count
    return snapshot


@router.post("/api/v1/queue/resume")
async def resume_queue(request: Request):
    await request.app.state.scheduler.resume()
    return await queue_status(request)
