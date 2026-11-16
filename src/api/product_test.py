"""Account-local SKU diagnostics, independent of scheduling and protection state."""
import asyncio
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from error_details import exception_details, response_details
from product_sku.client import ProductSkuClient
from request_identity import chrome_identity

from .sku import _authorize
from .sku_http import canonical_url

router = APIRouter(dependencies=[Depends(_authorize)])


class ProductTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_url: str = Field(strict=True, min_length=1, max_length=4096)


class DiagnosticRequestControl:
    """Collect existing diagnostics without observing account protection signals."""

    def __init__(self):
        self.events = []

    def before_request(self):
        pass

    def observe_response(self, response):
        pass

    def observe_result(self, result):
        pass

    def observe_error(self, exc):
        self.events.append({"exception": exception_details(exc),
                            "http": response_details(getattr(exc, "response", None))})

    def capture_details(self, details):
        self.events.append(details)


def fetch_product(url, records, account_id, proxy_url, user_agent, control):
    with ProductSkuClient(
        cookie_records=records, proxy_url=proxy_url, user_agent=user_agent,
        browser_profile=chrome_identity(account_id), network_retries=0,
        request_control=control,
    ) as client:
        return client.fetch(url).to_dict()


@router.post("/api/v1/accounts/{account_id}/product-test")
async def run_product_test(account_id: str, body: ProductTestRequest, request: Request):
    url = canonical_url(body.product_url)
    database = request.app.state.database
    account = await database.get_account_state(account_id)
    if account is None:
        raise HTTPException(404, detail="账号不存在")
    if account["state"] == "deleting":
        raise HTTPException(409, detail={"code": "ACCOUNT_DELETING", "message": "账号正在删除"})

    started = perf_counter()
    control = DiagnosticRequestControl()
    result = {"kind": "account_product_test", "account_id": account_id,
              "product_url": url, "cookie_version": None, "success": False,
              "result": None, "error": None, "diagnostics": {"events": control.events}}
    try:
        version, records = await request.app.state.cookie_store.load_active(account_id)
        result["cookie_version"] = version
        user_agent = await database.get_account_user_agent(account_id)
        raw = await asyncio.to_thread(
            fetch_product, url, records, account_id, account["proxy_url"], user_agent, control,
        )
        result["result"] = raw
        result["success"] = raw["status"] in {"success", "partial_success"}
        if not result["success"]:
            result["error"] = {"code": raw["status"], "message": raw["reason"]}
    except Exception as exc:
        result["error"] = exception_details(exc)
        result["diagnostics"]["http"] = response_details(getattr(exc, "response", None))
    result["elapsed_ms"] = round((perf_counter() - started) * 1000)
    return result
