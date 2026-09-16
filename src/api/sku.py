"""SKU HTTP route, independent of image-search tasks and persistence."""
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from product_sku.urls import normalize_url

from .auth import require_api_key
from .sku_schemas import ProductSkuRequest, ProductSkuResponse
from .sku_service import SkuServiceError

router = APIRouter()


async def _authorize(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    require_api_key(x_api_key, request.app.state.settings.search_api_key, scope="搜索")


@router.post(
    "/api/v1/product-skus",
    response_model=ProductSkuResponse,
    dependencies=[Depends(_authorize)],
    responses={
        401: {"description": "查询密钥无效"},
        422: {"description": "请求或详情链接无效"},
        429: {"description": "SKU 并发已满"},
        502: {"model": ProductSkuResponse, "description": "上游受限、解析或网络失败；内部异常使用 detail"},
        503: {"description": "Cookie 不可用、上游要求登录或服务停止"},
        504: {"description": "等待超时；后台操作结束前仍占用并发槽"},
    },
)
async def query_product_skus(
    body: ProductSkuRequest, request: Request, response: Response,
) -> ProductSkuResponse:
    try:
        _product_id, canonical = normalize_url(body.product_url)
    except ValueError:
        raise HTTPException(422, detail={
            "code": "INVALID_PRODUCT_URL", "message": "需要标准 1688 商品详情链接",
        }) from None
    try:
        result = await request.app.state.sku_service.query(canonical)
        payload = ProductSkuResponse.model_validate(result.to_dict())
    except SkuServiceError as exc:
        raise HTTPException(
            exc.status_code, detail={"code": exc.code, "message": exc.message},
        ) from None
    except Exception:
        raise HTTPException(502, detail={
            "code": "SKU_QUERY_FAILED", "message": "SKU 查询失败",
        }) from None
    response.status_code = {
        "success": 200, "partial_success": 200, "source_not_applicable": 200,
        "login_required": 503, "access_restricted": 502,
        "parse_failed": 502, "network_failed": 502,
    }[payload.status]
    return payload
