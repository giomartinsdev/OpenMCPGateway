"""History, rollback, the audit CSV, and calling one tool from the panel.

Three things an operator does to a gateway they already own, none of which is a
new kind of write: read its stream, re-apply an older configuration, and run one
tool with the credential the gateway already holds.
"""

import json
from typing import Any

from gateway.application.container import Container
from gateway.domain.model import Gateway
from gateway.domain.readmodels import RequestRow

#: Fields a restore deliberately does not carry. Named here so a reader can see
#: the boundary rather than infer it from what is missing in the event.
_NOT_RESTORED = frozenset({"client_secret", "upstream_token", "api_keys", "enabled"})


async def history(container: Container, gateway: dict[str, Any]) -> list[dict[str, Any]]:
    """The gateway's own stream, oldest first, with secrets redacted.

    One aggregate's log, not the global feed filtered: "who changed this
    target_url, and what was it before" is a question about a stream, and the
    audit page is the wrong shape for it.
    """
    events = await container.store.load_stream(gateway["id"])
    return [
        {
            "sequence": event.sequence,
            "event_type": event.EVENT,
            "actor": event.actor,
            "occurred_at": event.occurred_at.isoformat(),
            "changes": _redact(event.payload()),
        }
        for event in events
    ]


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Never hand a secret back through a read endpoint.

    The event is history and keeps the value; the panel only needs to know that
    the field changed. A hash would still be a fingerprint of a live credential,
    so the key is kept and the value replaced with a marker.
    """
    return {
        key: ("***" if key in _NOT_RESTORED or "secret" in key or "token" in key
              or key in {"key_hash", "sealed"} else value)
        for key, value in payload.items()
    }


async def configuration_at(
    container: Container, gateway: dict[str, Any], version: int
) -> dict[str, Any]:
    """What the gateway looked like after `version`, for the panel's preview."""
    events = await container.store.load_stream(gateway["id"])
    return _snapshot(Gateway.from_events(gateway["id"], [
        event for event in events if event.sequence <= version
    ]))


def _snapshot(gateway: Gateway) -> dict[str, Any]:
    return {
        "version": gateway.version,
        "name": gateway.name,
        "target_url": gateway.target_url,
        "description": gateway.description,
        "requests_per_minute": gateway.requests_per_minute,
        "monthly_quota": gateway.monthly_quota,
        "tools": list(gateway.tools),
        "auth_mode": gateway.auth_mode.value,
        "enabled": gateway.enabled,
    }


async def call_tool(
    container: Container, gateway: dict[str, Any], *,
    tool: str, arguments: Any, actor: str,
) -> dict[str, Any]:
    """Run one tool on the upstream, as the gateway would, and record it.

    Not through `/g/<slug>/mcp`: that path needs a key's plaintext, and an
    operator's session is not a credential the proxy accepts. So this reaches the
    upstream the same way the prober does — with the gateway's own upstream token
    — and then appends the same `request.observed` row a proxied call would,
    naming the operator as the subject. The alternative, a silent unlogged call
    from the panel, is the one thing a playground must not be.
    """
    from fastmcp.client import Client

    allowed = tuple(gateway.get("tools") or ())
    if allowed and tool not in allowed:
        # Refused before the upstream sees it, and with the same sentence the
        # guard uses, so the panel does not grow a second vocabulary.
        return {
            "ok": False,
            "status": 403,
            "reason": "tool not in allowlist",
            "error": f"tool '{tool}' is not exposed by gateway '{gateway['slug']}'",
        }

    if gateway.get("auth_mode") == "api_key" and not gateway.get("api_keys"):
        return {
            "ok": False,
            "status": 401,
            "reason": "no live key",
            "error": "this gateway has no live API key for the panel to call with",
        }

    events = await container.store.load_stream(gateway["id"])
    upstream_token = ""
    for event in events:
        upstream_token = event.payload().get("upstream_token") or upstream_token

    config: dict[str, Any] = {"mcpServers": {gateway["slug"]: {
        "url": gateway["target_url"],
        "transport": "http",
        **({"headers": {"Authorization": f"Bearer {upstream_token}"}}
           if upstream_token else {}),
    }}}

    started = _now()
    error = ""
    result: Any = None
    try:
        async with Client(config, timeout=30.0) as client:
            result = await client.call_tool(tool, arguments or {})
    except Exception as exc:
        error = str(exc).strip().splitlines()[0][:300] or exc.__class__.__name__
    duration_ms = int((_now() - started) * 1000)

    # Recorded either way: a failed playground call is the most interesting row
    # the traffic log can hold, and an unlogged success would appear as a mystery
    # request from the panel's own address.
    await container.recorder.record(
        gateway_id=gateway["id"],
        gateway_slug=gateway["slug"],
        subject=f"operator:{actor}",
        outcome="allowed" if not error else "error",
        status=200 if not error else 502,
        duration_ms=duration_ms,
        tool=tool,
        method="PLAYGROUND",
        reason="" if not error else "upstream error",
    )

    if error:
        return {"ok": False, "status": 502, "reason": "upstream error",
                "error": error, "duration_ms": duration_ms}
    return {"ok": True, "status": 200, "result": _serialize(result),
            "duration_ms": duration_ms}


def _serialize(result: Any) -> Any:
    """FastMCP's result object, flattened into what the panel can print.

    Fallback to `str` rather than raising: a tool result that cannot be
    serialized is still worth showing, and a playground that 500s on an exotic
    content type is a playground nobody trusts.
    """
    data = getattr(result, "data", None)
    if data is not None:
        try:
            json.dumps(data)
        except (TypeError, ValueError):
            return str(data)
        return data
    return str(result)


def _now() -> float:
    import time

    return time.perf_counter()


async def recent(container: Container, gateway_id: str) -> list[RequestRow]:
    return await container.read_models.request_tail(gateway_id, 20)


__all__ = ["call_tool", "configuration_at", "history", "recent"]
