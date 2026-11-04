"""The payload trace: how big a body was, and a preview safe to keep.

Two jobs, one module, because they are the same question asked twice: what
crossed the proxy?

* **Sizes** are exact and cheap — a counter over the bytes already in memory.
* **Previews** are a redacted, capped prefix. They exist so "it returned
  something odd" is answered from the log, and they are capped here rather than
  in the column because a database default cannot stop a caller from writing a
  megabyte into a table that gets exported.

The redaction is deliberately shallow: it walks the JSON and replaces values
whose *key* names a credential. A secret hidden inside an opaque string cannot be
found by any rule short of pattern-matching every provider's token format, and
guessing at that is how a preview starts leaking in new and interesting ways.
"""

from __future__ import annotations

import json
from typing import Any

#: Longest preview kept, in characters. One screen of a drawer, and small enough
#: that a page of fifty rows stays a page and not a download.
PREVIEW_LIMIT = 1024

#: A key containing any of these is a credential, whatever its exact spelling —
#: `access_token`, `client_secret` and `TOKEN` are the same mistake.
_SECRET_WORDS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "private_key",
)

_REDACTED = "<redacted>"

def is_secret_key(name: str) -> bool:
    lowered = name.lower()
    return any(word in lowered for word in _SECRET_WORDS)

def redact(value: Any) -> Any:
    """Replace credential values, leaving the shape of the payload readable."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if is_secret_key(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value

def preview(body: bytes, limit: int = PREVIEW_LIMIT) -> str:
    """A capped, redacted prefix of `body`, or "" when there is nothing to show.

    JSON is re-serialized compactly so the preview is one line: a drawer that has
    to render a pretty-printed megabyte is a drawer that hangs. Anything that is
    not JSON is shown as text, because a malformed body is exactly the case
    someone is debugging.
    """
    if not body:
        return ""
    text = _as_text(body)
    if text is None:
        return ""
    return _cap(text, limit)

def _as_text(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        decoded = body.decode("utf-8", errors="replace").strip()
        return decoded or None
    # A lone string is valid JSON and serializes back to a quoted string, which
    # is noisier than the text itself.
    if isinstance(payload, str):
        return payload
    return json.dumps(redact(payload), ensure_ascii=False, separators=(",", ":"))

def _cap(text: str, limit: int) -> str:
    """Cut to `limit` characters, ending with an ellipsis that says it was cut.

    A silent cut is worse than no preview: the reader would conclude the payload
    ended there, which is the opposite of the truth.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"

__all__ = ["PREVIEW_LIMIT", "is_secret_key", "preview", "redact"]
