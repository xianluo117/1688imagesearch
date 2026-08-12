from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class SearchTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: HttpUrl


class TaskCreated(BaseModel):
    task_id: str
    status: Literal["queued"]


class ProductResult(BaseModel):
    image: str | None
    title: str | None
    price: str | None
    sale_quantity: str | int | float | None
    product_url: str | None


class SearchResultPayload(BaseModel):
    search_page_url: str
    image_id: str
    found: int | None
    products: list[ProductResult] = Field(max_length=3)


class TaskStatusResponse(BaseModel):
    task_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: float
    updated_at: float
    started_at: float | None = None
    finished_at: float | None = None
    result: SearchResultPayload | None = None
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
    worker_limit: int
    cookie: dict[str, Any]
    tasks: dict[str, int]
