"""Task-bound request-control contract; scheduling and persistence live in the API.

Callbacks are synchronous. before_request must return only after permission and
cancellation/deadline checks. Observers may update shared state or abort the task.
Observer methods are optional at runtime. No callback is retried by clients.
Injected custom transports must not perform hidden retries or redirects.
"""
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class RequestControl(Protocol):
    def before_request(self) -> None: ...

    def observe_response(self, response: Any) -> None: ...

    def observe_error(self, exc: Exception) -> None: ...

    def observe_result(self, result: Any) -> None: ...


class _CallbackAbort(BaseException):
    """Keep callback exceptions out of transport/business retry handlers."""

    def __init__(self, error: Exception):
        self.error = error
        self.traceback = error.__traceback__


def notify(control: RequestControl | None, method: str, *args: Any) -> None:
    if control is None:
        return
    try:
        callback = getattr(control, method, None)
        if callback is None and method == "before_request":
            raise TypeError("request_control requires before_request()")
        if callback is not None:
            callback(*args)
    except Exception as exc:
        raise _CallbackAbort(exc) from None


@contextmanager
def control_scope() -> Iterator[None]:
    """Restore the exact callback exception at the public client boundary."""
    try:
        yield
    except _CallbackAbort as abort:
        raise abort.error.with_traceback(abort.traceback) from None


def disable_transport_retries(session: Any) -> None:
    """Disable requests/urllib3 retries, including preconfigured borrowed sessions.

    curl_cffi's standard Session has no automatic request retry. Custom injected
    sessions/adapters remain responsible for the single-network-attempt contract.
    """
    if isinstance(session, requests.Session):
        for adapter in session.adapters.values():
            if isinstance(adapter, HTTPAdapter):
                adapter.max_retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
