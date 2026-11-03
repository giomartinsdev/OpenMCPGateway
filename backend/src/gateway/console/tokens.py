"""Read-only console tokens: who minted it, and how long it lives.

A separate token type from the operator's session cookie on purpose:

* The cookie is a browser credential (HttpOnly, SameSite, 12 hours). An agent's
  config file is neither HttpOnly nor short-lived, so it gets its own thing.
* The token carries **no tenant and no permissions**. Both are resolved on every
  call, exactly like the panel: revoking a role must take effect on the agent's
  next question, not on the next mint.

It is signed with `OMG_SESSION_SECRET` — one secret to rotate, and rotating it
revokes every console token and every browser session together. That is the
honest trade: no token table to leak, and no per-token revocation. Ninety days,
because an agent config that breaks weekly gets a token pasted into a wiki.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

#: Long enough not to be a weekly chore, short enough that a leaked config
#: expires within a quarter.
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90

_PURPOSE = "console"


class BadToken(ValueError):
    """Not a token this deployment issued, or no longer valid."""


@dataclass(frozen=True)
class ConsoleToken:
    operator_id: str
    email: str
    expires_at: int


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()


def mint(*, secret: str, operator_id: str, email: str,
         ttl_seconds: int = TOKEN_TTL_SECONDS) -> str:
    body = {
        "sub": operator_id,
        "email": email,
        "purpose": _PURPOSE,
        "issued": int(time.time()),
        "exp": int(time.time()) + ttl_seconds,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":")).encode()
    ).decode()
    return f"omgc_{encoded}.{_sign(encoded.encode(), secret)}"


def read(token: str, *, secret: str) -> ConsoleToken:
    if not token.startswith("omgc_"):
        raise BadToken("not a console token")
    encoded, _, signature = token[len("omgc_"):].partition(".")
    if not encoded or not signature:
        raise BadToken("malformed token")
    if not hmac.compare_digest(signature, _sign(encoded.encode(), secret)):
        raise BadToken("bad signature")
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadToken("undecodable token") from exc
    if body.get("purpose") != _PURPOSE:
        raise BadToken("wrong purpose")
    if int(body.get("exp", 0)) < int(time.time()):
        raise BadToken("token expired")
    return ConsoleToken(
        operator_id=str(body["sub"]),
        email=str(body["email"]),
        expires_at=int(body["exp"]),
    )


__all__ = ["TOKEN_TTL_SECONDS", "BadToken", "ConsoleToken", "mint", "read"]
