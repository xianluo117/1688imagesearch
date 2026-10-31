"""Authenticated queue observability, operator recovery and asynchronous SKU API."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

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


class QueueCancelRequest(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=500)


async def queue_task_summary(task_id: str, database) -> dict | None:
    task = await database.get_queue_task(task_id)
    if task is None:
        return None
    return {
        "seq": task.seq,
        "task_id": task.task_id,
        "account_id": task.account_id,
        "kind": task.kind,
        "status": task.status,
        "dependency_id": task.dependency_id,
        "cancel_requested": task.cancel_requested,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
    }


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


@router.get("/api/v1/accounts")
async def list_accounts(request: Request):
    return {"accounts": await request.app.state.database.list_accounts()}


@router.post("/api/v2/sku-tasks", response_model=SkuTaskResponse, status_code=202)
async def create_sku_task(body: ProductSkuRequest, request: Request, response: Response):
    url, key = canonical_url(body.product_url), idempotency_key(request)
    try:
        task_id = await request.app.state.sku_service.submit(url, idempotency_key=key, account_id=body.account_id)
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


@router.get("/api/v1/queue/tasks")
async def queue_tasks(
    request: Request,
    status: Literal["active", "all"] = Query(default="active"),
):
    statuses = ("queued", "running") if status == "active" else None
    return {"tasks": await request.app.state.database.list_queue_tasks(statuses)}


@router.post("/api/v1/queue/cancel")
async def cancel_queue_tasks(body: QueueCancelRequest, request: Request):
    database = request.app.state.database
    tasks = []
    for task_id in body.task_ids:
        await database.cancel_queue_task(task_id)
        task = await queue_task_summary(task_id, database)
        tasks.append(task or {"task_id": task_id, "status": "not_found"})
    return {"tasks": tasks}


@router.post("/api/v1/queue/resume")
async def resume_queue(request: Request):
    await request.app.state.scheduler.resume()
    return await queue_status(request)
