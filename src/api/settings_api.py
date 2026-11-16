"""Authenticated runtime settings and atomic publication on the application loop."""
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import ConfigurationError
from .setting_page import SETTING_PAGE
from .sku import _authorize


class SchedulingSettings(BaseModel):
    global_worker_count: int = Field(strict=True, ge=1, le=32)
    account_max_concurrency: int = Field(strict=True, ge=1, le=32)
    request_interval_min_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    request_interval_max_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    account_request_interval_min_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)
    account_request_interval_max_seconds: float = Field(gt=0, le=3600, allow_inf_nan=False)


def current_values(settings):
    return {name: getattr(settings, name) for name in SchedulingSettings.model_fields}


def publish_settings(app, settings):
    scheduler = app.state.scheduler
    scheduler.guard.update_settings(settings)
    scheduler.settings = settings
    app.state.settings = settings


async def load_settings(app):
    values = await app.state.database.get_runtime_settings()
    publish_settings(app, replace(app.state.settings, **values))


router = APIRouter()


@router.get('/setting', response_class=HTMLResponse, include_in_schema=False)
async def setting_page():
    return HTMLResponse(SETTING_PAGE)


@router.get('/api/v1/settings', dependencies=[Depends(_authorize)])
async def get_settings(request: Request):
    return current_values(request.app.state.settings)


@router.put('/api/v1/settings', dependencies=[Depends(_authorize)])
async def save_settings(body: SchedulingSettings, request: Request):
    app = request.app
    async with app.state.scheduler.settings_lock:
        values = body.model_dump()
        try:
            settings = replace(app.state.settings, **values)
        except ConfigurationError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        await app.state.database.save_runtime_settings(values)
        publish_settings(app, settings)
        return current_values(settings)
