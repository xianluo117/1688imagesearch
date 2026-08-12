from __future__ import annotations

import hmac

from fastapi import HTTPException, status


def require_api_key(provided: str | None, expected: str, *, scope: str) -> None:
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_API_KEY", "message": f"{scope} API Key 无效"},
            headers={"WWW-Authenticate": "ApiKey"},
        )
