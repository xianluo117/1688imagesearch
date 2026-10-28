"""DingTalk custom robot configuration, signing and alert deduplication."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DingTalkConfig:
    access_token: str
    secret: str
    updated_at: float


class DingTalkError(RuntimeError):
    pass


class DingTalkNotifier:
    def __init__(self, database, *, dedupe_seconds: float = 3600.0) -> None:
        self.database = database
        self.dedupe_seconds = dedupe_seconds

    @staticmethod
    def sign(secret: str, timestamp: int) -> str:
        value = f"{timestamp}\n{secret}".encode("utf-8")
        digest = hmac.new(secret.encode("utf-8"), value, hashlib.sha256).digest()
        return quote(base64.b64encode(digest).decode("ascii"), safe="")

    async def get_config(self) -> DingTalkConfig | None:
        return await self.database.get_dingtalk_config()

    async def save_config(self, access_token: str, secret: str) -> DingTalkConfig:
        return await self.database.save_dingtalk_config(access_token.strip(), secret.strip())

    async def send_text(self, content: str) -> dict:
        config = await self.get_config()
        if config is None:
            raise DingTalkError("尚未配置钉钉机器人")
        timestamp = int(time.time() * 1000)
        url = (
            "https://oapi.dingtalk.com/robot/send?access_token="
            f"{quote(config.access_token, safe='')}&timestamp={timestamp}&sign="
            f"{self.sign(config.secret, timestamp)}"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json={"msgtype": "text", "text": {"content": content}})
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode", 0) != 0:
            raise DingTalkError(payload.get("errmsg") or "钉钉机器人发送失败")
        return payload

    async def alert(self, reason: str, *, task_kind: str, task_id: str, message: str) -> bool:
        now = time.time()
        if not await self.database.claim_dingtalk_alert(reason, now - self.dedupe_seconds, now):
            return False
        content = (
            "1688 账号告警\n"
            f"原因：{reason}\n"
            f"任务类型：{task_kind}\n"
            f"任务 ID：{task_id}\n"
            f"详情：{message}"
        )
        try:
            await self.send_text(content)
        except Exception:
            await self.database.release_dingtalk_alert(reason)
            raise
        return True

    async def alert_safe(self, reason: str, *, task_kind: str, task_id: str, message: str) -> None:
        try:
            await self.alert(reason, task_kind=task_kind, task_id=task_id, message=message)
        except Exception:
            LOGGER.exception("钉钉告警发送失败: %s", reason)
