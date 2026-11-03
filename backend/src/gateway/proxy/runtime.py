"""Proxy runtime: one mounted FastMCP app per gateway, rebuilt on config change.

Why this shape:

* **One sub-app per gateway** rather than a single server with a routing tool.
  Each gateway needs its own OAuth issuer (`/g/<slug>`), its own target URL and
  its own allowlist. Mounting gives each a real origin for OAuth discovery, so
  standard MCP clients work without a custom auth story.
* **Guard middleware on the outside** distinguishes 401 (bad/missing credential)
  from 403 (known caller, not allowed) and 429 (over limit) and records every
  request. FastMCP still enforces bearer auth underneath — defence in depth,
  not the only check.
* **Rebuild, not restart.** A config change swaps the mounted app and enters the
  new lifespan before the old one is torn down, so a rolling update loses no
  in-flight session.

The pieces live in their own modules — `rate_limit`, `identity`, `guard`,
`providers`, `http` — and are re-exported here so callers do not care.
"""

from gateway.proxy.guard import GuardMiddleware
from gateway.proxy.identity import (
    GatewayAuth,
    GatewayNotReady,
    Identity,
    IdentityDenied,
)
from gateway.proxy.providers import build_auth_provider, forget_client_store
from gateway.proxy.rate_limit import RateLimiter

__all__ = [
    "GatewayAuth",
    "GatewayNotReady",
    "GuardMiddleware",
    "Identity",
    "IdentityDenied",
    "RateLimiter",
    "build_auth_provider",
    "forget_client_store",
]
