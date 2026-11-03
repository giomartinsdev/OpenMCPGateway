"""The manual: one copy of the text, read by the panel and by the console MCP.

The prose lives in `sections.json` rather than in a Python literal so it stays a
document — one paragraph per line, easy to diff in review, no line-length
argument with the linter. Two consumers:

* `GET /api/docs` → the panel's documentation page
* the console MCP's `search_docs` → what an agent reads before guessing

A second copy in `web/src` is what this replaces: the JSX page used to own the
text, which meant an agent could not read the manual at all.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_SECTIONS_FILE = Path(__file__).with_name("docs") / "sections.json"


@lru_cache(maxsize=1)
def sections() -> tuple[dict[str, Any], ...]:
    """Every section, in reading order. Loaded once: it ships with the wheel."""
    return tuple(json.loads(_SECTIONS_FILE.read_text(encoding="utf-8"))["sections"])


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Sections whose text contains *every* word of `query`.

    Naive on purpose: ten sections of a few hundred words each. A rank-free
    substring match answers better than an index nobody would maintain, and
    requiring every word keeps "oauth allowlist" from returning every section that
    mentions OAuth. Title hits sort first.
    """
    words = [word.lower() for word in query.split() if word.strip()]
    if not words:
        return []

    hits: list[tuple[int, dict[str, Any]]] = []
    for section in sections():
        text = _flatten(section).lower()
        if all(word in text for word in words):
            title_hit = all(word in section["title"].lower() for word in words)
            hits.append((0 if title_hit else 1, section))
    hits.sort(key=lambda pair: pair[0])
    return [
        {"id": section["id"], "title": section["title"], "text": _flatten(section)}
        for _rank, section in hits[: max(1, min(limit, 20))]
    ]


def _flatten(section: dict[str, Any]) -> str:
    parts: list[str] = list(section.get("body") or [])
    parts.extend(section.get("bodyAfter") or [])
    table = section.get("table")
    if table:
        parts.extend(" | ".join(str(cell) for cell in row) for row in table["rows"])
    return "\n".join(parts)


__all__ = ["search", "sections"]
