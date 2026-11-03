"""Structural check: every manual translation is the English manual, translated.

Run it after editing the prose, or after adding a language:

    .venv/bin/python -m gateway.docs_check

It is a script, not a test, because the failure it catches is a *release*
mistake (a section dropped, a code fence mangled), and the fix is always "edit
the JSON", never "change the code".
"""

from __future__ import annotations

import re
import sys

from gateway.docs_content import LANGUAGES, sections

FENCE = re.compile(r"```[a-z]*\n.*?```", re.DOTALL)
BACKTICK = re.compile(r"`[^`\n]+`")


def _flat(section: dict) -> list[str]:
    parts = list(section.get("body") or []) + list(section.get("bodyAfter") or [])
    table = section.get("table")
    if table:
        parts.extend(table["head"])
        parts.extend(cell for row in table["rows"] for cell in row)
    return parts


def _geometry(translations: list[dict], english: list[dict]) -> list[str]:
    errors: list[str] = []
    if [s["id"] for s in translations] != [s["id"] for s in english]:
        errors.append("section ids or order differ")
        return errors
    for translated, source in zip(translations, english, strict=True):
        where = source["id"]
        for key in ("body", "bodyAfter"):
            if len(translated.get(key) or []) != len(source.get(key) or []):
                errors.append(f"{where}: {key} paragraph count differs")
        a, b = translated.get("table"), source.get("table")
        if bool(a) != bool(b):
            errors.append(f"{where}: table presence differs")
        elif a:
            if len(a["head"]) != len(b["head"]):
                errors.append(f"{where}: table head differs")
            if [len(row) for row in a["rows"]] != [len(row) for row in b["rows"]]:
                errors.append(f"{where}: table rows differ")
    return errors


def _code(translations: list[dict], english: list[dict]) -> list[str]:
    """Fences and inline identifiers must survive verbatim, or the manual lies."""
    errors: list[str] = []
    for translated, source in zip(translations, english, strict=True):
        where = source["id"]
        prose_before = "\n".join(_flat(source))
        prose_after = "\n".join(_flat(translated))
        # Fences first: a ``` block contains backticks, so leaving it in makes the
        # inline-code scan below match across a whole snippet.
        before = FENCE.findall(prose_before)
        after = FENCE.findall(prose_after)
        if before != after:
            errors.append(f"{where}: code fences changed")
        tokens_before = set(BACKTICK.findall(FENCE.sub("", prose_before)))
        tokens_after = set(BACKTICK.findall(FENCE.sub("", prose_after)))
        if tokens_before != tokens_after:
            missing = tokens_before - tokens_after
            errors.append(f"{where}: inline code changed ({sorted(missing)[:3]})")
    return errors


def check() -> dict[str, list[str]]:
    english = list(sections("en"))
    problems: dict[str, list[str]] = {}
    for code in LANGUAGES:
        translated = list(sections(code))
        errors = _geometry(translated, english) + _code(translated, english)
        if errors:
            problems[code] = errors
    return problems


def main() -> int:
    problems = check()
    for code in LANGUAGES:
        mark = "ok" if code not in problems else f"{len(problems[code])} problem(s)"
        print(f"{code}: {mark}")
        for error in problems.get(code, []):
            print(f"  {error}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
