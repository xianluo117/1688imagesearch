"""SKU HTTP route, independent of image-search tasks and persistence."""
from fastapi import APIRouter, Depends, Header, Request, Response

from .auth import require_api_key
from .sku_http import RESULT_HTTP_STATUS, query_failed, query_result
from .sku_schemas import ProductSkuRequest, ProductSkuResponse

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
    result = await query_result(body.product_url, request)
    try:
        payload = ProductSkuResponse.model_validate(result.to_dict())
    except Exception:
        raise query_failed() from None
    response.status_code = RESULT_HTTP_STATUS[payload.status]
    return payload
