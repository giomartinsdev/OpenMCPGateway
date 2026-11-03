"""The manual: one copy of the text, read by the panel and by the console MCP.

The prose lives in `sections.json` rather than in a Python literal so it stays a
document — one paragraph per line, easy to diff in review, no line-length
argument with the linter. Two consumers:

* `GET /api/docs?lang=xx` → the panel's documentation page
* the console MCP's `search_docs` → what an agent reads before guessing

Each translation is a whole file, `sections.<lang>.json`, with the same ids and
the same shape. Not a table of strings keyed by paragraph index: a translator
who moves a sentence across a paragraph boundary should be able to, and the
review diff should read like the document it is. A missing or unreadable
translation falls back to English rather than to a blank page — the manual is
the one page that must always render.

A second copy in `web/src` is what this replaces: the JSX page used to own the
text, which meant an agent could not read the manual at all.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DOCS_DIR = Path(__file__).with_name("docs")
_DEFAULT = "en"

# The panel's locales, in the order the picker offers them. English is the
# source: it ships as `sections.json` and is the fallback for everything else.
LANGUAGES = ("en", "pt", "es", "zh", "ru")


def _file(lang: str) -> Path:
    return _DOCS_DIR / ("sections.json" if lang == _DEFAULT else f"sections.{lang}.json")


def normalise(lang: str | None) -> str:
    """`pt-BR` → `pt`, anything unknown → English."""
    code = (lang or "").split("-")[0].strip().lower()
    return code if code in LANGUAGES else _DEFAULT


@lru_cache(maxsize=len(LANGUAGES) * 2)
def sections(lang: str = _DEFAULT) -> tuple[dict[str, Any], ...]:
    """Every section, in reading order. Loaded once per language: it ships with
    the wheel, so there is nothing to invalidate."""
    code = normalise(lang)
    try:
        payload = json.loads(_file(code).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if code == _DEFAULT:
            raise
        return sections(_DEFAULT)
    return tuple(payload["sections"])


def search(query: str, limit: int = 5, lang: str = _DEFAULT) -> list[dict[str, Any]]:
    """Sections whose text contains *every* word of `query`.

    Naive on purpose: ten sections of a few hundred words each. A rank-free
    substring match answers better than an index nobody would maintain, and
    requiring every word keeps "oauth allowlist" from returning every section that
    mentions OAuth. Title hits sort first.

    Only one language is searched: the caller's. Matching across languages would
    answer a Portuguese question with an English paragraph, which reads like a
    bug even when the text is right.
    """
    words = [word.lower() for word in query.split() if word.strip()]
    if not words:
        return []

    hits: list[tuple[int, dict[str, Any]]] = []
    for section in sections(lang):
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


__all__ = ["LANGUAGES", "normalise", "search", "sections"]
