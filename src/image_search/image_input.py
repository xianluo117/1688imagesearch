from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageOps

from .errors import ProtocolError

DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_UPLOAD_BYTES = 300 * 1024


def detect_image_type(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    return None


def _compress_for_upload(content: bytes, *, target_bytes: int) -> bytes:
    try:
        with Image.open(io.BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ProtocolError(f"图片解码失败: {exc}") from exc

    max_edge = max(image.size)
    if max_edge > 1600:
        scale = 1600 / max_edge
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )

    for quality in (80, 70, 60, 50, 40, 30):
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        compressed = output.getvalue()
        if len(compressed) <= target_bytes:
            return compressed

    while max(image.size) > 320:
        image = image.resize(
            (max(1, round(image.width * 0.8)), max(1, round(image.height * 0.8))),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=45, optimize=True)
        compressed = output.getvalue()
        if len(compressed) <= target_bytes:
            return compressed

    raise ProtocolError(f"图片压缩后仍超过上传限制: {len(compressed)} 字节")


def encode_image_base64(
    content: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    upload_bytes: int = DEFAULT_UPLOAD_BYTES,
) -> tuple[str, str, int]:
    size = len(content)
    if size <= 0:
        raise ProtocolError("图片内容为空")
    if size > max_bytes:
        raise ProtocolError(f"图片内容过大: {size} 字节，限制为 {max_bytes} 字节")

    image_type = detect_image_type(content)
    if image_type is None:
        raise ProtocolError("不支持或无法识别的图片格式，仅支持 JPEG、PNG、GIF、WebP")
    if size > upload_bytes:
        content = _compress_for_upload(content, target_bytes=upload_bytes)
        image_type = "jpeg"
    return base64.b64encode(content).decode("ascii"), image_type, len(content)


def read_image_base64(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    upload_bytes: int = DEFAULT_UPLOAD_BYTES,
) -> tuple[str, str, int]:
    image_path = Path(path)
    if not image_path.is_file():
        raise ProtocolError(f"图片文件不存在: {image_path}")
    return encode_image_base64(
        image_path.read_bytes(),
        max_bytes=max_bytes,
        upload_bytes=upload_bytes,
    )
