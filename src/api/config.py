from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet


class ConfigurationError(RuntimeError):
    pass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value


def _non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value < 0:
        raise ConfigurationError(f"{name} 不能小于 0")
    return value


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value


def _required_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if len(value) < 24:
        raise ConfigurationError(f"{name} 未配置或长度不足 24 个字符")
    return value


@dataclass(frozen=True)
class Settings:
    cookie_upload_api_key: str
    search_api_key: str
    cookie_encryption_key: str
    database_path: Path
    upload_worker_count: int = 2
    product_worker_count: int = 2
    max_queued_tasks: int = 100
    upload_task_timeout_seconds: float = 240.0
    product_task_timeout_seconds: float = 180.0
    task_retention_seconds: int = 86400
    download_connect_timeout: float = 10.0
    download_timeout: float = 30.0
    max_image_bytes: int = 8 * 1024 * 1024
    search_http_timeout: float = 90.0
    search_network_retries: int = 1
    search_ready_retries: int = 8
    search_ready_interval: float = 1.5

    def __post_init__(self) -> None:
        if not 1 <= self.upload_worker_count <= 2:
            raise ConfigurationError("upload_worker_count 必须在 1 到 2 之间")
        if not 1 <= self.product_worker_count <= 2:
            raise ConfigurationError("product_worker_count 必须在 1 到 2 之间")
        if self.cookie_upload_api_key == self.search_api_key:
            raise ConfigurationError("Cookie 上传密钥与搜索密钥不能相同")
        if len(self.cookie_upload_api_key) < 24 or len(self.search_api_key) < 24:
            raise ConfigurationError("两类 API Key 长度均不能少于 24 个字符")

    @classmethod
    def from_env(cls) -> "Settings":
        encryption_key = _required_secret("COOKIE_ENCRYPTION_KEY")
        try:
            Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ConfigurationError(
                "COOKIE_ENCRYPTION_KEY 必须是 Fernet.generate_key() 生成的 URL-safe Base64 密钥"
            ) from exc

        return cls(
            cookie_upload_api_key=_required_secret("COOKIE_UPLOAD_API_KEY"),
            search_api_key=_required_secret("SEARCH_API_KEY"),
            cookie_encryption_key=encryption_key,
            database_path=Path(os.getenv("DATABASE_PATH", "data/image-search.db")),
            upload_worker_count=_positive_int("UPLOAD_WORKER_COUNT", 2),
            product_worker_count=_positive_int("PRODUCT_WORKER_COUNT", 2),
            max_queued_tasks=_positive_int("MAX_QUEUED_TASKS", 100),
            upload_task_timeout_seconds=_positive_float("UPLOAD_TASK_TIMEOUT_SECONDS", 240.0),
            product_task_timeout_seconds=_positive_float("PRODUCT_TASK_TIMEOUT_SECONDS", 180.0),
            task_retention_seconds=_positive_int("TASK_RETENTION_SECONDS", 86400),
            download_connect_timeout=_positive_float("DOWNLOAD_CONNECT_TIMEOUT", 10.0),
            download_timeout=_positive_float("DOWNLOAD_TIMEOUT", 30.0),
            max_image_bytes=_positive_int("MAX_IMAGE_BYTES", 8 * 1024 * 1024),
            search_http_timeout=_positive_float("SEARCH_HTTP_TIMEOUT", 90.0),
            search_network_retries=_non_negative_int("SEARCH_NETWORK_RETRIES", 1),
            search_ready_retries=_non_negative_int("SEARCH_READY_RETRIES", 8),
            search_ready_interval=_positive_float("SEARCH_READY_INTERVAL", 1.5),
        )
