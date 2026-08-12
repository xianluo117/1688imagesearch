from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from image_search.image_input import detect_image_type


class ImageDownloadError(RuntimeError):
    code = "IMAGE_DOWNLOAD_FAILED"


class UnsafeImageUrlError(ImageDownloadError):
    code = "IMAGE_URL_REJECTED"


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeImageUrlError("图片 URL 仅支持 HTTP 或 HTTPS")
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeImageUrlError("图片 URL 主机无效或包含用户凭据")
    if parsed.port is not None and parsed.port not in {80, 443}:
        raise UnsafeImageUrlError("图片 URL 仅允许 80 或 443 端口")

    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_address(str(literal)):
            raise UnsafeImageUrlError("图片 URL 指向非公网地址")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ImageDownloadError("图片 URL 域名解析失败") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise UnsafeImageUrlError("图片 URL 域名解析到非公网地址")


async def download_image(
    url: str,
    *,
    max_bytes: int,
    connect_timeout: float,
    total_timeout: float,
    max_redirects: int = 3,
) -> bytes:
    current_url = url
    timeout = httpx.Timeout(total_timeout, connect=connect_timeout)
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0 Safari/537.36"
        ),
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for redirect_count in range(max_redirects + 1):
            await validate_public_url(current_url)
            try:
                async with client.stream("GET", current_url, headers=headers) as response:
                    network_stream = response.extensions.get("network_stream")
                    peer = (
                        network_stream.get_extra_info("server_addr")
                        if network_stream is not None
                        else None
                    )
                    if peer and not _is_public_address(str(peer[0])):
                        raise UnsafeImageUrlError("图片下载实际连接到非公网地址")

                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= max_redirects:
                            raise ImageDownloadError("图片 URL 重定向次数过多")
                        location = response.headers.get("location")
                        if not location:
                            raise ImageDownloadError("图片重定向响应缺少 Location")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code != 200:
                        raise ImageDownloadError(f"图片下载返回 HTTP {response.status_code}")

                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if not content_type.startswith("image/"):
                        raise ImageDownloadError("远程资源 Content-Type 不是图片")
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared = int(content_length)
                        except ValueError:
                            declared = 0
                        if declared > max_bytes:
                            raise ImageDownloadError("远程图片超过体积限制")

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ImageDownloadError("远程图片超过体积限制")
                    content = bytes(body)
                    if not content:
                        raise ImageDownloadError("远程图片内容为空")
                    if detect_image_type(content) is None:
                        raise ImageDownloadError("远程内容不是支持的 JPEG、PNG、GIF 或 WebP 图片")
                    return content
            except httpx.TimeoutException as exc:
                raise ImageDownloadError("图片下载超时") from exc
            except httpx.HTTPError as exc:
                raise ImageDownloadError(f"图片下载网络错误: {type(exc).__name__}") from exc
    raise ImageDownloadError("图片下载失败")
