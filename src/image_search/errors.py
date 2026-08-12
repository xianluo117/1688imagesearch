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
