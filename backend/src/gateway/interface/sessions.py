"""Operator sessions — signed, stateless, and not a bearer token.

The admin UI is a browser app, so a cookie is the right carrier: it survives
reloads, works with the SPA router, and cannot be lifted by a leaked `fetch`
response the way a token in localStorage can. It is HttpOnly + SameSite=Lax and
signed with `OMG_SESSION_SECRET`, so no server-side session table is needed.

`SameSite=Lax` (not Strict) is deliberate: the login redirect from Google/GitHub
comes from another origin and Strict would drop the cookie on return.
"""

import base64
import hashlib
import hmac
import json
import time

from pydantic import BaseModel, ConfigDict

SESSION_COOKIE = "omg_session"
SESSION_TTL_SECONDS = 60 * 60 * 12


class SessionError(RuntimeError):
    """Cookie is absent, forged, or expired."""


class Session(BaseModel):
    """Who is signed in. Frozen: a session is a claim about the past.

    `tenant_id` is constant today (one deployment is one tenant) and carried
    anyway: every authorisation decision names the tenant it is about, so
    multi-tenancy is a change here rather than a change in each endpoint.
    """

    model_config = ConfigDict(frozen=True)

    operator_id: str
    email: str
    is_admin: bool
    tenant_id: str
    expires_at: int

    @property
    def role(self) -> str:
        return "admin" if self.is_admin else "member"


def _sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def issue_session(*, secret: str, operator_id: str, email: str,
                  is_admin: bool, tenant_id: str,
                  ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    body = {
        "sub": operator_id,
        "email": email,
        "admin": is_admin,
        "tenant": tenant_id,
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
        # A cookie issued before tenants existed has no claim; it belongs to the
        # deployment's tenant like every other account.
        tenant_id=body.get("tenant") or "tn_local",
        expires_at=int(body["exp"]),
    )


__all__ = [
    "SESSION_COOKIE",
    "Session",
    "SessionError",
    "issue_session",
    "read_session",
]
