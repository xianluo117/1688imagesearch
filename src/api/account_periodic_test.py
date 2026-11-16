"""Independent periodic diagnostics for opted-in paused accounts."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .product_test import test_account_product
from .sku import _authorize
from .sku_http import canonical_url

router = APIRouter(dependencies=[Depends(_authorize)])
logger = logging.getLogger(__name__)


class PeriodicTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(strict=True)
    product_url: str | None = Field(default=None, max_length=4096)


@router.put("/api/v1/accounts/{account_id}/periodic-test")
async def save_periodic_test(account_id: str, body: PeriodicTestRequest, request: Request):
    # Stopping does not depend on any unsaved browser input.
    url = canonical_url(body.product_url or "") if body.enabled else None
    database = request.app.state.database
    if not await database.set_account_periodic_test(account_id, body.enabled, url):
        raise HTTPException(409, detail="账号不存在或正在删除")
    return await database.get_account_state(account_id)


class AccountPeriodicTestService:
    interval = 30

    def __init__(self, database, cookie_store):
        self.database = database
        self.cookie_store = cookie_store
        self._stopping = asyncio.Event()
        self._loop_task = None
        self._tasks = {}

    async def start(self):
        self._stopping.clear()
        self._loop_task = asyncio.create_task(self._run())

    async def stop(self):
        self._stopping.set()
        if self._loop_task is not None:
            await self._loop_task
            self._loop_task = None
        # Do not cancel to_thread: its underlying request would keep running.
        await asyncio.gather(*self._tasks.values())
        self._tasks.clear()

    async def _run(self):
        while not self._stopping.is_set():
            try:
                await self.scan()
            except Exception:
                logger.exception("Periodic account scan failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    async def scan(self):
        for account in await self.database.periodic_test_candidates():
            if self._stopping.is_set():
                break
            account_id = account["account_id"]
            previous = self._tasks.get(account_id)
            if previous is None or previous.done():
                self._tasks[account_id] = asyncio.create_task(self._probe(account))
        self._tasks = {key: task for key, task in self._tasks.items() if not task.done()}

    async def _probe(self, account):
        try:
            result = await test_account_product(
                self.database, self.cookie_store, account, account["periodic_test_url"],
            )
            if result["success"] and not self._stopping.is_set():
                await self.database.recover_product_test(account, periodic=True)
        except Exception:
            # Infrastructure failures stay local to this account's task.
            logger.exception("Periodic account test failed: %s", account["account_id"])
