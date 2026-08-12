from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from image_search.cookies import (
    ImportedCookie,
    parse_cookie_payload,
    validate_cookie_names,
)
from image_search.errors import ProtocolError

from .database import Database


class CookieStoreError(RuntimeError):
    pass


class CookieStore:
    def __init__(self, database: Database, encryption_key: str) -> None:
        self.database = database
        self.fernet = Fernet(encryption_key.encode("ascii"))

    @staticmethod
    def _validate_payload(payload: Any) -> tuple[list[ImportedCookie], dict[str, Any]]:
        records = parse_cookie_payload(payload)
        names = {record.name for record in records}
        validate_cookie_names(names)
        metadata = payload if isinstance(payload, dict) else {}
        return records, {
            "cookie_count": len(records),
            "exported_at": metadata.get("exportedAt"),
            "has_cookie1": "cookie1" in names,
            "has_cookie2": "cookie2" in names,
            "has_m_h5_tk": "_m_h5_tk" in names,
            "has_m_h5_tk_enc": "_m_h5_tk_enc" in names,
        }

    async def save(self, payload: Any) -> dict[str, Any]:
        records, metadata = self._validate_payload(payload)
        serialized = json.dumps(
            {"cookies": [asdict(record) for record in records]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encrypted = self.fernet.encrypt(serialized)
        version = await self.database.store_cookie(
            encrypted,
            cookie_count=len(records),
            exported_at=metadata["exported_at"],
        )
        return {"version": version, **metadata}

    async def _decode_row(
        self,
        row: tuple[int, bytes, int, float] | None,
    ) -> tuple[int, list[ImportedCookie]]:
        if row is None:
            raise CookieStoreError("指定的 Cookie 版本不存在")
        version, encrypted, _cookie_count, _uploaded_at = row
        try:
            payload = json.loads(self.fernet.decrypt(encrypted).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CookieStoreError("Cookie 密文无法解密或内容损坏") from exc
        try:
            records = parse_cookie_payload(payload)
            validate_cookie_names({record.name for record in records})
        except ProtocolError as exc:
            raise CookieStoreError(f"已存储 Cookie 无效: {exc}") from exc
        return version, records

    async def load_active(self) -> tuple[int, list[ImportedCookie]]:
        row = await self.database.get_active_cookie()
        if row is None:
            raise CookieStoreError("尚未上传 1688 Cookie")
        return await self._decode_row(row)

    async def load_version(self, version: int) -> tuple[int, list[ImportedCookie]]:
        return await self._decode_row(await self.database.get_cookie_version(version))

    async def status(self) -> dict[str, Any]:
        row = await self.database.get_active_cookie()
        if row is None:
            return {"available": False}
        version, _encrypted, cookie_count, uploaded_at = row
        return {
            "available": True,
            "version": version,
            "cookie_count": cookie_count,
            "uploaded_at": uploaded_at,
        }
