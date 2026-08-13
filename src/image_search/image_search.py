from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from curl_cffi import requests as curl_requests

from .cookies import ImportedCookie, load_cookie_records_into_session, load_cookies
from .errors import ProductsNotFoundError, ProtocolError, TaskCancelledError, UploadNoImageIdError
from .image_input import DEFAULT_MAX_IMAGE_BYTES, encode_image_base64, read_image_base64
from .mtop import MtopClient, MtopRequest, compact_json
from .parser import ParsedPage, Product, parse_page

LOGGER = logging.getLogger(__name__)
RECOMMEND_API = "mtop.relationrecommend.wirelessrecommend.recommend"
RECOMMEND_VERSION = "2.0"
APP_ID = 32517
APP_NAME = "pctusou"
SEARCH_SCENE = "pcImageSearch"


@dataclass(frozen=True)
class SearchOptions:
    page_size: int = 60
    result_limit: int = 3
    ready_retries: int = 5
    request_interval: float = 1.0
    exclude_ads: bool = False
    include_raw_item: bool = False
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES
    raw_response_dir: Path | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not 1 <= self.page_size <= 100:
            raise ProtocolError("page_size 必须在 1 到 100 之间")
        if self.result_limit < 1:
            raise ProtocolError("result_limit 必须大于 0")
        if self.ready_retries < 0:
            raise ProtocolError("ready_retries 不能小于 0")
        if self.request_interval < 0:
            raise ProtocolError("request_interval 不能小于 0")
        if self.max_image_bytes <= 0:
            raise ProtocolError("max_image_bytes 必须大于 0")


@dataclass(frozen=True)
class UploadContext:
    image_id: str
    session_id: str | None
    request_id: str | None
    pvid: str | None
    trace_id: str | None


@dataclass(frozen=True)
class SearchResult:
    upload: UploadContext
    products: list[Product]
    pages_requested: int
    found: int | None
    stop_reason: str

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        return {
            "image_id": self.upload.image_id,
            "session_id": self.upload.session_id,
            "request_id": self.upload.request_id,
            "pvid": self.upload.pvid,
            "trace_id": self.upload.trace_id,
            "pages_requested": self.pages_requested,
            "found": self.found,
            "stop_reason": self.stop_reason,
            "product_count": len(self.products),
            "products": [product.to_dict(include_raw=include_raw) for product in self.products],
        }


class ImageSearchClient:
    def __init__(
        self,
        cookie_file: str | Path | None = None,
        *,
        cookie_records: list[ImportedCookie] | None = None,
        options: SearchOptions | None = None,
        session: Any | None = None,
        timeout: float = 30.0,
        network_retries: int = 2,
    ) -> None:
        if (cookie_file is None) == (cookie_records is None):
            raise ProtocolError("cookie_file 与 cookie_records 必须且只能提供一个")
        self.options = options or SearchOptions()
        self.options.validate()
        self.session = session or curl_requests.Session(impersonate="chrome")
        self.cookie_names = (
            load_cookies(self.session, cookie_file)
            if cookie_file is not None
            else load_cookie_records_into_session(self.session, cookie_records or [])
        )
        self._raw_sequence = 0
        self.mtop = MtopClient(
            self.session,
            timeout=timeout,
            network_retries=network_retries,
            response_hook=self._archive_response if self.options.raw_response_dir else None,
        )

    def _archive_response(self, request: MtopRequest, payload: dict[str, Any]) -> None:
        directory = self.options.raw_response_dir
        if directory is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        self._raw_sequence += 1
        safe_api = request.api.replace(".", "_")
        path = directory / f"{self._raw_sequence:03d}-{safe_api}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _business_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ProtocolError("MTOP 响应缺少 data 对象")
        return data

    def _call_recommend(self, params: dict[str, Any], *, search_request: bool = False) -> dict[str, Any]:
        request_data = {"appId": APP_ID, "params": compact_json(params)}
        extra_query = {"jsonpIncPrefix": "reqTppId_32517_getOfferList"}
        if search_request:
            extra_query["callback"] = "mtopjsonpreqTppId_32517_getOfferList1"
        return self.mtop.request(
            MtopRequest(
                api=RECOMMEND_API,
                version=RECOMMEND_VERSION,
                data=request_data,
                method="GET" if search_request else "POST",
                request_type="jsonp" if search_request else "originaljson",
                extra_query=extra_query,
            )
        )

    def _upload_encoded_image(
        self,
        image_base64: str,
        image_type: str,
        image_size: int,
    ) -> UploadContext:
        LOGGER.info("读取图片完成: type=%s size=%d", image_type, image_size)
        params = {
            "beginPage": 1,
            "pageSize": self.options.page_size,
            "searchScene": SEARCH_SCENE,
            "method": "uploadBase64WithRequest",
            "appName": APP_NAME,
            "imageBase64": image_base64,
        }
        payload = self._call_recommend(params)
        business = self._business_data(payload)
        nested = business.get("data")
        nested = nested if isinstance(nested, dict) else {}
        image_id = nested.get("imageId") or business.get("imageId")
        if not image_id:
            raise UploadNoImageIdError("上传成功响应中缺少 imageId")
        return UploadContext(
            image_id=str(image_id),
            session_id=_optional_text(business.get("sessionId")),
            request_id=_optional_text(business.get("requestId")),
            pvid=_optional_text(business.get("pvid")),
            trace_id=_optional_text(payload.get("traceId") or business.get("tpp_trace")),
        )

    def upload_image(self, image_path: str | Path) -> UploadContext:
        encoded = read_image_base64(
            image_path,
            max_bytes=self.options.max_image_bytes,
        )
        return self._upload_encoded_image(*encoded)

    def upload_image_bytes(self, content: bytes) -> UploadContext:
        encoded = encode_image_base64(
            content,
            max_bytes=self.options.max_image_bytes,
        )
        return self._upload_encoded_image(*encoded)

    def _search_params(self, image_id: str, page: int) -> dict[str, Any]:
        method = "getImageSearchPreResult" if page == 1 else "imageOfferSearchService"
        params: dict[str, Any] = {
            "beginPage": page if page == 1 else str(page),
            "pageSize": self.options.page_size,
            "method": method,
            "searchScene": SEARCH_SCENE,
            "appName": APP_NAME,
            "tab": "imageSearch",
            "imageId": image_id,
            "imageIdList": image_id,
            "spm": "a260k.home2025/2025.imagesearch.upload",
        }
        params.update(self.options.extra_params)
        return params

    def search_page(self, image_id: str, page: int) -> ParsedPage:
        if page < 1:
            raise ProtocolError("页码必须大于 0")
        payload = self._call_recommend(self._search_params(image_id, page), search_request=True)
        return parse_page(payload, include_raw=self.options.include_raw_item)

    def search_uploaded(
        self,
        upload: UploadContext,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SearchResult:
        page: ParsedPage | None = None
        pages_requested = 0

        for attempt in range(self.options.ready_retries + 1):
            if cancel_check and cancel_check():
                raise TaskCancelledError("商品任务已取消")
            if attempt > 0 and self.options.request_interval:
                time.sleep(self.options.request_interval)
            if cancel_check and cancel_check():
                raise TaskCancelledError("商品任务已取消")
            LOGGER.info("请求图搜首屏，尝试 %d/%d", attempt + 1, self.options.ready_retries + 1)
            candidate = self.search_page(upload.image_id, 1)
            pages_requested += 1
            if cancel_check and cancel_check():
                raise TaskCancelledError("商品任务已取消")
            if candidate is not None and candidate.products:
                page = candidate
                break

        if page is None:
            raise ProductsNotFoundError("图片搜索未返回商品")

        products: list[Product] = []
        seen: set[str] = set()
        for product in page.products:
            if product.offer_id in seen:
                continue
            seen.add(product.offer_id)
            if self.options.exclude_ads and product.is_ad:
                continue
            products.append(product)
            if len(products) >= self.options.result_limit:
                break

        return SearchResult(
            upload=upload,
            products=products,
            pages_requested=pages_requested,
            found=page.found,
            stop_reason="result_limit",
        )

    def search_image_id(
        self,
        image_id: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SearchResult:
        if not image_id.strip():
            raise ProtocolError("image_id 不能为空")
        return self.search_uploaded(
            UploadContext(
                image_id=image_id,
                session_id=None,
                request_id=None,
                pvid=None,
                trace_id=None,
            ),
            cancel_check=cancel_check,
        )

    def search(self, image_path: str | Path) -> SearchResult:
        return self.search_uploaded(self.upload_image(image_path))

    def search_bytes(self, content: bytes) -> SearchResult:
        return self.search_uploaded(self.upload_image_bytes(content))


def _optional_text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)
