"""Operator sessions — signed, stateless, and not a bearer token.

The admin UI is a browser app, so a cookie is the right carrier: it survives
reloads, works with the SPA router, and cannot be lifted by a leaked `fetch`
response the way a token in localStorage can. It is HttpOnly + SameSite=Lax and
signed with `OMG_SESSION_SECRET`, so no server-side session table is needed.

`SameSite=Lax` (not Strict) is deliberate: the login redirect from Google/GitHub
comes from another origin and Strict would drop the cookie on return.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

SESSION_COOKIE = "omg_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


class SessionError(RuntimeError):
    """Cookie is absent, forged, or expired."""


@dataclass(frozen=True, slots=True)
class Session:
    operator_id: str
    email: str
    is_admin: bool
    expires_at: int


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def issue_session(*, secret: str, operator_id: str, email: str,
                  is_admin: bool, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    body = {
        "sub": operator_id,
        "email": email,
        "admin": is_admin,
        "exp": int(time.time()) + ttl_seconds,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(body, separators=(",", ":")).encode()
    ).decode()
    return f"{encoded}.{_sign(encoded.encode(), secret)}"


def read_session(token: str, *, secret: str) -> Session:
    encoded, _, signature = token.partition(".")
    if not encoded or not signature:
        raise SessionError("malformed session")
    expected = _sign(encoded.encode(), secret)
    if not hmac.compare_digest(signature, expected):
        raise SessionError("bad signature")
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SessionError("undecodable session") from exc
    if int(body.get("exp", 0)) < int(time.time()):
        raise SessionError("session expired")
    return Session(
        operator_id=body["sub"],
        email=body["email"],
        is_admin=bool(body.get("admin")),
        expires_at=int(body["exp"]),
    )


__all__ = ["Session", "SessionError", "SESSION_COOKIE", "issue_session",
           "read_session"]
