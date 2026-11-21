"""Grammar / spelling / style check via the free public LanguageTool API (no key needed).

Limits of the public endpoint: 20 requests/min, 20k chars/request, 75 KB/min. For heavy
use run LanguageTool locally (docker: erikvl87/languagetool) and set LANGUAGETOOL_URL.
"""
from __future__ import annotations

import logging
import re

import httpx

from . import config

log = logging.getLogger(__name__)


async def check_grammar(text: str, language: str = "auto") -> dict:
    truncated = False
    if len(text) > config.LANGUAGETOOL_MAX_CHARS:
        text = text[: config.LANGUAGETOOL_MAX_CHARS]
        truncated = True
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                config.LANGUAGETOOL_URL,
                data={
                    "text": text,
                    "language": language,
                    "preferredVariants": config.LANGUAGETOOL_VARIANTS if language == "auto" else "",
                    "enabledOnly": "false",
                    "level": "picky",
                },
                headers={"User-Agent": "burstai/0.1"},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("LanguageTool failed: %s", e)
        return {"available": False, "error": str(e), "issues": [], "counts": {}}

    issues = []
    counts: dict[str, int] = {}
    for m in data.get("matches", []):
        cat = (m.get("rule", {}).get("category", {}) or {}).get("id", "OTHER")
        issue_type = m.get("rule", {}).get("issueType", "")
        kind = _bucket(cat, issue_type)
        flagged = text[m["offset"] : m["offset"] + m["length"]]
        if _noise(m, kind, flagged):
            continue
        counts[kind] = counts.get(kind, 0) + 1
        issues.append(
            {
                "offset": m["offset"],
                "length": m["length"],
                "message": m.get("message", ""),
                "short": m.get("shortMessage") or kind.title(),
                "kind": kind,
                "replacements": [r["value"] for r in m.get("replacements", [])[:5]],
                "rule": m.get("rule", {}).get("id", ""),
                "context": m.get("context", {}).get("text", ""),
            }
        )
    lang = (data.get("language") or {}).get("detectedLanguage", {}).get("name") or (data.get("language") or {}).get("name")
    return {"available": True, "language": lang, "issues": issues, "counts": counts, "truncated": truncated}


_VARIANT_HINT = re.compile(r"\b(British|American|Canadian|Australian) English\b", re.I)
_TECHY = re.compile(r"^(?=.*[a-z])(?=.*[A-Z]).*[A-Z].*$|^[A-Z0-9][A-Z0-9&.+-]{1,}$|\d|[A-Za-z]+[-/][A-Za-z]+")
_NOISY_RULES = {"WHITESPACE_RULE", "CONSECUTIVE_SPACES", "DOUBLE_PUNCTUATION", "UNPAIRED_BRACKETS", "COMMA_PARENTHESIS_WHITESPACE"}


def _noise(m: dict, kind: str, flagged: str) -> bool:
    """Drop suggestions that are almost always wrong for real-world text.

    * cosmetic whitespace / punctuation-spacing rules (pasted text is often reflowed)
    * spelling flags on CamelCase, acronyms, product names, hyphenated tech terms (GenAI-powered)
    * British/American variant nags - both are correct English
    * spelling fixes whose replacement barely resembles the word (Gas-powered for GenAI-powered)
    """
    rule = m.get("rule", {}).get("id", "")
    if rule in _NOISY_RULES or rule.startswith("WHITESPACE"):
        return True
    if kind != "spelling":
        return False
    word = flagged.strip()
    if not word or _TECHY.search(word):
        return True
    if _VARIANT_HINT.search(m.get("message", "")):
        return True
    reps = [r["value"] for r in m.get("replacements", [])[:3]]
    if reps:
        from rapidfuzz import fuzz

        if max(fuzz.ratio(word.lower(), r.lower()) for r in reps) < 72:
            return True
    return False


def _bucket(category: str, issue_type: str) -> str:
    c = category.upper()
    if "TYPO" in c or issue_type == "misspelling":
        return "spelling"
    if "GRAMMAR" in c or issue_type in ("grammar", "uncategorized"):
        return "grammar"
    if "PUNCT" in c or "TYPOGRAPHY" in c:
        return "punctuation"
    if "STYLE" in c or "REDUNDANCY" in c or "PLAIN" in c or issue_type == "style":
        return "style"
    return "other"
