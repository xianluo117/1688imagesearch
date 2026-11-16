"""Independent asynchronous egress check; never changes account state."""
from time import perf_counter

from curl_cffi import requests

from request_identity import chrome_identity


async def test_proxy(proxy_url: str, account_id: str | None = None, *, user_agent: str | None = None) -> dict:
    started = perf_counter()
    result = {"success": False, "mode": "proxy" if proxy_url else "direct", "ip": None, "error": None}
    try:
        async with requests.AsyncSession(
            impersonate=chrome_identity(account_id), trust_env=False, proxies={"all": proxy_url},
        ) as session:
            if user_agent:
                session.headers["User-Agent"] = user_agent
            response = await session.get("https://icanhazip.com", timeout=15)
            response.raise_for_status()
            result.update(success=True, ip=response.text.strip())
    except (requests.RequestsError, ValueError, KeyError) as exc:
        result["error"] = str(exc)
    result["elapsed_ms"] = round((perf_counter() - started) * 1000)
    return result
