"""V2 route: one shared query followed by a pure response conversion."""
from fastapi import APIRouter, Depends, Request, Response

from .sku import _authorize
from .sku_http import RESULT_HTTP_STATUS, query_failed, query_result
from .sku_schemas import ProductSkuRequest
from .sku_v2_converter import convert_result
from .sku_v2_schemas import ProductSkuResponseV2

router = APIRouter()


@router.post(
    "/api/v2/product-skus",
    response_model=ProductSkuResponseV2,
    dependencies=[Depends(_authorize)],
    responses={
        401: {"description": "查询密钥无效；detail 错误"},
        422: {"description": "请求或详情链接无效；detail 错误"},
        429: {"description": "共享 SKU 并发已满；detail 错误"},
        502: {"model": ProductSkuResponseV2, "description": "上游受限、解析或网络失败；内部异常使用 detail"},
        503: {"model": ProductSkuResponseV2, "description": "上游要求登录；Cookie 不可用或服务停止使用 detail"},
        504: {"description": "等待超时；detail 错误，后台结束前仍占共享并发槽"},
    },
)
async def query_product_skus_v2(
    body: ProductSkuRequest, request: Request, response: Response,
) -> ProductSkuResponseV2:
    result = await query_result(body.product_url, request)
    try:
        payload = convert_result(result)
    except Exception:
        raise query_failed() from None
    response.status_code = RESULT_HTTP_STATUS[payload.status]
    return payload
