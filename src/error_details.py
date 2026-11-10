"""Per-execution diagnostics, explicitly shared with the worker thread."""
import traceback


def exception_details(exc):
    return {
        "exception_type": f"{type(exc).__module__}.{type(exc).__name__}",
        "message": str(exc),
        "traceback": ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "upstream": getattr(exc, 'payload', None),
        "http": getattr(exc, 'http_details', None),
    }


def response_details(response, *, body=None, complete=False, reason=None):
    if response is None:
        return {"response_received": False}
    headers = getattr(response, 'headers', {}) or {}
    if body is None:
        # Only inspect an already buffered body. Never consume a stream here.
        content = getattr(response, '__dict__', {}).get('_content')
        if isinstance(content, bytes):
            body, complete = content, True
        else:
            content = getattr(response, '__dict__', {}).get('content')
            if isinstance(content, bytes):
                body, complete = content, True
    return {
        "response_received": True,
        "status_code": getattr(response, 'status_code', None),
        "url": str(getattr(response, 'url', '') or ''),
        "location": headers.get('Location', headers.get('location')),
        "body": body.decode('utf-8', errors='replace') if isinstance(body, bytes) else body,
        "body_complete": complete,
        "incomplete_reason": None if complete else (reason or '响应正文未读取或未完整读取'),
    }
