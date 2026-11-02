"""What a caller has to send to use a gateway, as runnable code.

Why this is a service and not a React template: the OAuth path is a chain of
*server* endpoints (`/register`, `/authorize`, `/token`) served by the proxy, plus
per-gateway scopes. A snippet generated in the browser would be a second copy of
that knowledge, and the copy in the panel would be the one that goes stale.

The panel just renders what this returns. Nothing here reads a secret: the API
key snippet uses a placeholder, because the plaintext is shown once at issue time
and never stored.
"""

import json
from typing import Any

from gateway.application.container import Container
from gateway.domain.events import AuthMode


def _initialize(endpoint: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "manual-test", "version": "1"},
        },
    }


def _post(endpoint: str, *, bearer: str | None = None) -> str:
    """A curl POST that speaks MCP: the Accept header is not optional.

    Without `text/event-stream` the streamable-HTTP transport answers 406, which
    looks like an auth problem and is not.
    """
    headers = [
        '-H "Content-Type: application/json"',
        '-H "Accept: application/json, text/event-stream"',
    ]
    if bearer:
        headers.append(f'-H "Authorization: Bearer {bearer}"')
    body = json.dumps(_initialize(endpoint), indent=2)
    indented = "\n  ".join(body.splitlines())
    return (
        f"curl -sS -X POST '{endpoint}' \\\n  "
        + " \\\n  ".join(headers)
        + f" \\\n  -d '{indented}'"
    )


def _python(endpoint: str, *, bearer: str | None = None) -> str:
    """The same call through the official SDK, which is what a real client uses.

    `streamable_http_client` + `ClientSession` is the whole protocol; hand-rolled
    JSON loses session headers and SSE framing on the second call.
    """
    auth = ""
    if bearer:
        auth = (
            "    async with create_mcp_http_client(\n"
            f'        headers={{"Authorization": f"Bearer {{os.environ[\'{bearer}\']}}"}}\n'
            "    ) as http_client:\n"
        )
    indent = "        " if bearer else "    "
    inner = (
        f"{indent}async with streamable_http_client(\n"
        f'{indent}    "{endpoint}"'
        + (", http_client=http_client" if bearer else "")
        + f"\n{indent}) as (read, write, _):"
    )
    return (
        "import asyncio\n"
        + ("import os\n" if bearer else "")
        + "\nfrom mcp import ClientSession\n"
        "from mcp.client.streamable_http import streamable_http_client\n"
        + ("from mcp.shared._httpx_utils import create_mcp_http_client\n" if bearer else "")
        + "\n\nasync def main() -> None:\n"
        + auth
        + f"{inner}\n"
        f"{indent}    async with ClientSession(read, write) as session:\n"
        f"{indent}        await session.initialize()\n"
        f"{indent}        print(await session.list_tools())\n\n\n"
        "asyncio.run(main())"
    )


def _step(title: str, description: str, code: str) -> dict[str, str]:
    return {"title": title, "description": description, "code": code}


async def guide(container: Container, gateway: dict[str, Any]) -> dict[str, Any]:
    """Two runnable paths — `curl` and Python — for one gateway."""
    endpoint = f"{container.settings.base_url}/g/{gateway['slug']}/mcp"
    mode = AuthMode(gateway["auth_mode"])

    if mode is AuthMode.API_KEY:
        return {
            "endpoint": endpoint,
            "auth_mode": mode.value,
            "curl": [
                _step(
                    "Call the gateway with a key",
                    "Create one under API keys on this page; it is shown once. "
                    "The key is a bearer token, so it identifies the caller and "
                    "counts against the rate limit per key.",
                    _post(endpoint, bearer="$OMG_API_KEY"),
                )
            ],
            "python": [
                _step(
                    "List the tools",
                    "The SDK does the JSON-RPC for you. Export the key first: "
                    "export OMG_API_KEY=...",
                    _python(endpoint, bearer="OMG_API_KEY"),
                )
            ],
        }

    if mode is AuthMode.OAUTH:
        return _oauth_guide(container, gateway, endpoint)

    return {
        "endpoint": endpoint,
        "auth_mode": mode.value,
        "curl": [
            _step(
                "Call the gateway",
                "This gateway is public: no credential is required. Anyone with "
                "the URL can call it, so keep the allowlist empty only on purpose.",
                _post(endpoint),
            )
        ],
        "python": [
            _step(
                "List the tools",
                "The same call through the official MCP client.",
                _python(endpoint),
            )
        ],
    }


def _oauth_guide(container: Container, gateway: dict[str, Any],
                 endpoint: str) -> dict[str, Any]:
    """The full chain: discovery, register, authorize, token, then call.

    A browser client hides all of this behind one library call, which is exactly
    why a manual test needs it spelled out: "the request 401s" is usually the
    fifth step, not the first.
    """
    base = container.settings.base_url.rstrip("/")
    prefix = f"{base}/g/{gateway['slug']}"
    resource_metadata = (
        f"{base}/.well-known/oauth-protected-resource/g/{gateway['slug']}/mcp"
    )
    issuer_metadata = f"{base}/.well-known/oauth-authorization-server/g/{gateway['slug']}"
    scopes = list(gateway.get("required_scopes") or [])
    scope_line = f" \\\n  -d 'scope={' '.join(scopes)}'" if scopes else ""
    redirect = "http://localhost:8765/callback"

    return {
        "endpoint": endpoint,
        "auth_mode": "oauth",
        "idp": gateway.get("idp"),
        "issuer": prefix,
        "scopes": scopes,
        "curl": [
            _step(
                "1. See the challenge",
                "An unauthenticated call answers 401 and names the resource "
                "metadata. It is not an error to fix — it is the start of the "
                "OAuth flow.",
                _post(endpoint),
            ),
            _step(
                "2. Read the discovery documents",
                "The first names this gateway as the resource, and points at the "
                "authorization server. The second lists the endpoints used below.",
                f"curl -sS '{resource_metadata}' | python -m json.tool\n\n"
                f"curl -sS '{issuer_metadata}' | python -m json.tool",
            ),
            _step(
                "3. Register this client",
                "Dynamic Client Registration: no operator has to create an app "
                "for you. The response carries a client_id to use in the next step.",
                f"""curl -sS -X POST '{prefix}/register' \\
  -H 'Content-Type: application/json' \\
  -d '{{
    "client_name": "manual-test",
    "redirect_uris": ["{redirect}"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none"
  }}'""",
            ),
            _step(
                "4. Authorize in the browser",
                "Open this URL (the provider's login page). On success it "
                "redirects to your callback with a one-time `code`.",
                f"{prefix}/authorize?response_type=code"
                f"&client_id=$CLIENT_ID&redirect_uri={redirect}"
                f"&code_challenge=$CODE_CHALLENGE&code_challenge_method=S256"
                + (f"&scope={'%20'.join(scopes)}" if scopes else ""),
            ),
            _step(
                "5. Exchange the code for a token",
                "`code_verifier` is the value the challenge in step 4 was hashed "
                "from. The token is short-lived; a 401 later means get a new one.",
                f"""curl -sS -X POST '{prefix}/token' \\
  -H 'Content-Type: application/x-www-form-urlencoded' \\
  -d 'grant_type=authorization_code' \\
  -d 'code=$CODE' \\
  -d 'client_id=$CLIENT_ID' \\
  -d 'redirect_uri={redirect}' \\
  -d 'code_verifier=$CODE_VERIFIER'{scope_line}""",
            ),
            _step(
                "6. Call the gateway with the token",
                "Only after a token exists does the MCP call get through.",
                _post(endpoint, bearer="$ACCESS_TOKEN"),
            ),
        ],
        "python": [
            _step(
                "Let the SDK run the flow",
                "The official client performs steps 1-5 itself: it reads the "
                "challenge, registers, opens the browser and stores the token. "
                "This is the shortest correct test.",
                _python(endpoint),
            ),
            _step(
                "Or bring your own token",
                "Useful when the browser is not available (CI, a server). Get an "
                "access token with the curl steps first, then export it.",
                _python(endpoint, bearer="OMG_ACCESS_TOKEN"),
            ),
        ],
    }


__all__ = ["guide"]
