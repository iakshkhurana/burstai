"""Grammar / spelling / style check via the free public LanguageTool API (no key needed).

Limits of the public endpoint: 20 requests/min, 20k chars/request, 75 KB/min. For heavy
use run LanguageTool locally (docker: erikvl87/languagetool) and set LANGUAGETOOL_URL.
"""
from __future__ import annotations

import logging

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
