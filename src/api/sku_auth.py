"""Authenticate SKU routes without expanding operator permissions."""
import hmac

from fastapi import Header, Request

from .auth import require_api_key


async def authorize_sku(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str | None:
    settings = request.app.state.settings
    if x_api_key and x_api_key.isascii():
        for client in settings.sku_client_keys:
            if hmac.compare_digest(x_api_key, client.secret):
                return client.name
    require_api_key(x_api_key if x_api_key and x_api_key.isascii() else None, settings.search_api_key, scope="搜索")
    return None
