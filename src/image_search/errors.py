from __future__ import annotations

from typing import Any


class ProtocolError(RuntimeError):
    """协议调用或响应结构不符合预期。"""


class MtopError(ProtocolError):
    """MTOP 返回业务错误。"""

    def __init__(self, message: str, *, ret: list[str] | None = None, payload: Any = None):
        super().__init__(message)
        self.ret = ret or []
        self.payload = payload


class TokenError(MtopError):
    """MTOP H5 Token 缺失、过期或校验失败。"""


class AuthenticationError(MtopError):
    """1688 登录态无效。"""


class RiskControlError(MtopError):
    """请求触发验证码、风控或访问限制。"""


class RateLimitError(MtopError):
    """请求触发服务端限流。"""


class MtopNetworkError(ProtocolError):
    """MTOP HTTP 请求因网络或服务端错误失败。"""


class UploadNoImageIdError(ProtocolError):
    """图片上传响应未返回 imageId。"""


class ProductsNotFoundError(ProtocolError):
    """图片搜索在轮询期限内没有返回商品。"""


class TaskCancelledError(RuntimeError):
    """后台任务收到协作式取消请求。"""
