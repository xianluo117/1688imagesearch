"""Shared SKU HTTP policy; both versions use the same service and admission."""
from fastapi import HTTPException, Request

from product_sku.models import SkuResult
from product_sku.urls import normalize_url

from .sku_service import SkuServiceError

RESULT_HTTP_STATUS = {
    "success": 200, "partial_success": 200, "source_not_applicable": 200,
    "login_required": 503, "access_restricted": 502,
    "parse_failed": 502, "network_failed": 502,
}


async def query_result(product_url: str, request: Request) -> SkuResult:
    try:
        _product_id, canonical = normalize_url(product_url)
    except ValueError:
        raise HTTPException(422, detail={
            "code": "INVALID_PRODUCT_URL", "message": "需要标准 1688 商品详情链接",
        }) from None
    try:
        return await request.app.state.sku_service.query(canonical)
    except SkuServiceError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": exc.message},
        ) from None
    except Exception:
        raise query_failed() from None


def query_failed() -> HTTPException:
    return HTTPException(502, detail={"code": "SKU_QUERY_FAILED", "message": "SKU 查询失败"})
