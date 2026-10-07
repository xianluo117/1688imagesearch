"""Explicit one-GET price diagnosis with optional in-memory offline recheck."""
import argparse
import asyncio
import importlib
import time
from pathlib import Path
from unittest.mock import patch

import diagnose_product_sku as diagnosis
from product_sku.client import ProductSkuClient
from product_sku.price_diagnostics import inspect
from product_sku.urls import normalize_url


def emit(event, **fields):
    # Absent fixed candidates carry no additional evidence; keep logs readable.
    if event == "fixed_quote_container" and not fields["present"]:
        return
    diagnosis.emit(event, **fields)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--url", required=True)
    arguments.add_argument("--recheck-signal", help="New non-secret file signals reload and offline recheck")
    options = arguments.parse_args()
    product_id, url = normalize_url(options.url)
    signal = Path(options.recheck_signal) if options.recheck_signal else None
    if signal and signal.exists():
        raise ValueError("signal_already_exists")
    retained = []
    calls = 0
    original_fetch = ProductSkuClient.fetch

    def inspect_page(text, address):
        # Client's bounded decoded response body stays exclusively in this process.
        retained.append((text, address))
        return inspect(text, address, product_id, emit, samples=True)

    def fetch_once(client, address):
        original_get = client.session.get

        def get(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise ValueError("diagnostic_request_limit")
            return original_get(*args, **kwargs)

        with patch.object(client.session, "get", get):
            return original_fetch(client, address)

    diagnosis.PRODUCT_ID, diagnosis.URL = product_id, url
    with patch.object(diagnosis, "inspect_page", inspect_page), patch.object(ProductSkuClient, "fetch", fetch_once):
        asyncio.run(diagnosis.run())
    emit("request_budget", calls=calls, retained_responses=len(retained))
    if signal and retained:
        emit("memory_recheck_waiting", timeout_seconds=600)
        deadline = time.monotonic() + 600
        while not signal.exists() and time.monotonic() < deadline:
            time.sleep(1)
        if signal.exists():
            for name in ("product_sku.prices", "product_sku.quote_sources", "product_sku.parser",
                         "product_sku.price_evidence", "product_sku.price_diagnostics"):
                importlib.reload(importlib.import_module(name))
            from product_sku.price_diagnostics import inspect as reloaded_inspect
            emit("memory_recheck_start", additional_requests=0)
            for text, address in retained:
                reloaded_inspect(text, address, product_id, emit, samples=True)
        else:
            emit("memory_recheck_expired", additional_requests=0)
    retained.clear()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("failure", category=type(exc).__name__)
        raise SystemExit(1) from None
