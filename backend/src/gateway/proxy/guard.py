@"""The guard is the one place every gateway call passes through.

It authenticates, authorises (subject, tool, budget), observes, and only then
reaches the upstream. Everything it records about a call is recorded here, in
the `finally`, whether the call was allowed, refused, or raised.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from gateway.log import log
from gateway.observability import Metrics, RequestRecorder
from gateway.proxy.identity import GatewayAuth, Identity, is_key_subject
from gateway.proxy.payload import preview
from gateway.proxy.rate_limit import RateLimiter

_RESOURCE_METADATA = "/.well-known/oauth-protected-resource"

#: How much of a response body is retained to build a preview. The size is always
#: retained and counted; only the text is capped, because a streaming body is
#: read through by `_run_traced` and the preview is a debugging aid, not evidence.
_RESPONSE_PREVIEW_BYTES = 256 * 1024

#: What `_blocked_tool` answers for a body it cannot parse while an allowlist is
#: configured. `None` means "this request names no tool", and a body the guard
#: could not read is not a body it cleared: the upstream may accept what this
#: parse rejected, so it is refused instead of waved through.
_UNPARSEABLE = "<unparseable>"


class GuardMiddleware:
    """Auth + authorisation + rate limit + observation for one gateway mount."""

    def __init__(self, app: ASGIApp, *, config: dict[str, Any],
                 auth: GatewayAuth, limiter: RateLimiter,
                 recorder: RequestRecorder, metrics: Metrics, base_url: str,
                 read_models: Any = None, max_body_bytes: int = 1_048_576,
                 trusted_proxies: tuple[str, ...] = ()) -> None:
        self.app = app
        self.config = config
        self.auth = auth
        self.limiter = limiter
        self.recorder = recorder
        self.metrics = metrics
        #: Only touched when the gateway has a monthly quota; a gateway without
        #: one never reads it.
        self.read_models = read_models
        self.base_url = base_url.rstrip("/")
        self.prefix = f"/g/{config['slug']}"
        self.inner_path = "/mcp"
        #: The body is buffered so it can be inspected, so this is also the
        #: largest allocation one call can cause.
        self.max_body_bytes = max_body_bytes
        #: Peers whose `X-Forwarded-For` is believed. Empty means none.
        self.trusted_proxies = trusted_proxies
