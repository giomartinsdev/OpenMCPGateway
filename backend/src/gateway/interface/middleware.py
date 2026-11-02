"""Cross-cutting HTTP concerns that are not any route's business."""

import time
from typing import Any

from fastapi import Request

from gateway.log import log

#: Health probes and static files would be most of the log volume and none of
#: the signal.
_SKIP_PREFIXES = ("/assets/",)
_SKIP_PATHS = {"/healthz"}


async def log_requests(request: Request, call_next: Any) -> Any:
    """One line per admin/API request, with its status and duration.

    Gateway traffic is logged by the proxy guard instead — it knows the subject
    and the outcome, which this layer does not.
    """
    path = request.url.path
    if path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES):
        return await call_next(request)

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        log.error(
            "http.unhandled",
            path=path,
            method=request.method,
            error=repr(exc),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        raise
    log.info(
        "http.request",
        path=path,
        method=request.method,
        status=response.status_code,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return response


__all__ = ["log_requests"]
