from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

TaskStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class UploadTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: HttpUrl


class ProductTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_task_id: str = Field(min_length=1)


class UploadTaskCreated(BaseModel):
    task_id: str
    status: Literal["queued"]


class ProductTaskCreated(BaseModel):
    task_id: str
    upload_task_id: str
    status: Literal["queued"]


class UploadResultPayload(BaseModel):
    image_id: str
    search_page_url: str


class ProductResult(BaseModel):
    image: str | None
    title: str | None
    price: str | None
    sale_quantity: str | int | float | None
    product_url: str | None


class ProductSearchResultPayload(BaseModel):
    search_page_url: str
    image_id: str
    found: int | None
    products: list[ProductResult] = Field(max_length=3)


class UploadTaskStatusResponse(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: UploadResultPayload | None = None
    error: dict[str, str] | None = None


class ProductTaskStatusResponse(BaseModel):
    task_id: str
    upload_task_id: str
    status: TaskStatus
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: ProductSearchResultPayload | None = None
    error: dict[str, str] | None = None


class CookieUploadResponse(BaseModel):
    status: Literal["updated"]
    version: int
    cookie_count: int
    exported_at: str | None
    has_cookie1: bool
    has_cookie2: bool
    has_m_h5_tk: bool
    has_m_h5_tk_enc: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DetailedHealthResponse(BaseModel):
    status: Literal["ok"]
    cookie: dict[str, Any]
    upload_workers: int
    product_workers: int
    upload_tasks: dict[str, int]
    product_tasks: dict[str, int]
