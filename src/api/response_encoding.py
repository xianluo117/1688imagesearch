"""Declare UTF-8 explicitly for JSON clients with legacy decoding defaults."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class JsonCharsetMiddleware:
    """Add a charset to JSON response headers without rewriting body bytes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_charset(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "")
                parts = content_type.split(";")
                media_type = parts[0].strip().lower()
                is_json = media_type == "application/json" or (
                    media_type.startswith("application/") and media_type.endswith("+json")
                )
                has_charset = any(
                    part.split("=", 1)[0].strip().lower() == "charset"
                    for part in parts[1:]
                )
                if is_json and not has_charset:
                    headers["content-type"] = f"{content_type}; charset=utf-8"
            await send(message)

        await self.app(scope, receive, send_with_charset)
