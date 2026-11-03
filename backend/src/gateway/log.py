"""Structured logging: one JSON object per line, for Loki/Grafana.

Two properties matter more than pretty output:

* **A schema, not a string.** `event`, `component`, `duration_ms` are fields, so
  Grafana can group and alert on them. Anything a human needs to read is `msg`,
  which is never parsed.
* **Never fail the caller.** A log call must not be able to break a request.

Context (request id, gateway slug) is installed for the duration of a unit of
work with `bind()`, so a line 40 calls deep still says which gateway it was
about without threading a logger through every signature.
"""

import contextlib
import json
import logging
import os
import sys
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

#: Per-task context. `ContextVar` is the only correct choice: a global would mix
#: two concurrent requests' gateway ids.
_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "omg_log_context", default=None
)

#: Record attributes that belong to the logging machinery, not to our fields.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            **(_context.get() or {}),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key in payload or key.startswith("_"):
                continue
            payload[key] = value
        # Fields the caller passed, merged as data. Not spread into the record:
        # `logging` owns that namespace and raises on a collision.
        payload.update(record.__dict__.get("_omg_fields") or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class JsonLogger:
    """A thin facade over `logging`, plus the context that makes lines useful.

    Wrapping rather than subclassing keeps the standard library as the transport:
    uvicorn, SQLAlchemy and FastMCP all log through `logging`, and they end up in
    the same JSON stream because `configure()` rewrites the root handler.
    """

    def __init__(self, name: str = "gateway") -> None:
        self._log = logging.getLogger(name)

    def bind(self, **fields: Any) -> None:
        """Add fields to every subsequent line in this task."""
        _context.set({**(_context.get() or {}), **fields})

    def unbind(self, *names: str) -> None:
        context = dict(_context.get() or {})
        for name in names:
            context.pop(name, None)
        _context.set(context)

    @contextmanager
    def context(self, **fields: Any) -> AbstractContextManager[None]:
        """Scope extra fields to a block, restoring what was there before."""
        token = _context.set({**(_context.get() or {}), **fields})
        try:
            yield
        finally:
            _context.reset(token)

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(self._log.debug, event, fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(self._log.info, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(self._log.warning, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit(self._log.error, event, fields)

    @staticmethod
    def _emit(writer: Any, event: str, fields: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            # The fields travel as one dict rather than as `**fields`: `logging`
            # refuses to overwrite a reserved record attribute (`name`, `msg`,
            # `module`…) and raises `KeyError`, which `suppress` then swallows —
            # the whole line disappears. The formatter merges the dict instead,
            # where a field called `name` is just a key in the JSON object.
            writer(event, extra={"event": event, "_omg_fields": fields}, stacklevel=3)


def configure(level: str | None = None, *, stream: Any = None) -> None:
    """Install the JSON handler on the root logger. Idempotent."""
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        return
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel((level or os.environ.get("OMG_LOG_LEVEL", "info")).upper())
    # uvicorn installs plain-text handlers of its own; clear them so its access
    # log lands in the same JSON stream instead of a second format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The HTTP clients log one line per upstream call at INFO. That duplicates
    # `proxy.request` with less context, and one MCP call is several round
    # trips — the volume would drown the signal.
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # FastMCP's transport logs one INFO line per session on both ends. Useful
    # when debugging a handshake, useless as steady-state volume.
    for name in ("mcp", "mcp.server", "mcp.client"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.propagate = True


log = JsonLogger("gateway")

__all__ = ["JsonFormatter", "JsonLogger", "configure", "log"]
