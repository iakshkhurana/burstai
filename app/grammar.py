"""Grammar / spelling / style check via the free public LanguageTool API (no key needed).

Limits of the public endpoint: 20 requests/min, 20k chars/request, 75 KB/min. For heavy
use run LanguageTool locally (docker: erikvl87/languagetool) and set LANGUAGETOOL_URL.
"""
from __future__ import annotations

import logging
import json
import re

import httpx

from . import config, llm

log = logging.getLogger(__name__)


async def check_grammar(text: str, language: str = "auto", gemini_key: str | None = None) -> dict:
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
    reviewed = False
    if llm.available(gemini_key):
        refined = await _refine_with_gemini(text, issues, gemini_key)
        if refined is not None:
            issues = refined
            counts = {}
            for it in issues:
                counts[it["kind"]] = counts.get(it["kind"], 0) + 1
            reviewed = True
    return {"available": True, "language": lang, "issues": issues, "counts": counts, "truncated": truncated, "reviewed_by": config.GEMINI_MODEL if reviewed else None}


_REVIEW_PROMPT = """You are a careful copy editor reviewing an automated grammar checker's output for the TEXT below.

The automated checker proposed these issues (JSON):
%s

Your job:
1. KEEP only proposals that are genuine errors a good editor would fix in this context. DROP proposals about
   product names, technical terms, code identifiers, acronyms, proper nouns, British/American spelling variants,
   intentional formatting, headings in ALL CAPS, or anything that is actually fine.
2. ADD real errors the checker missed: wrong word (effects/affects, their/there/they're, its/it's), subject-verb
   agreement, tense, missing/extra words, obvious typos, wrong prepositions. Do NOT rewrite style or tone.
3. For every issue give the EXACT original substring as it appears in the text (copy it verbatim, keep its
   capitalisation and punctuation, 1-6 words), the replacement, a short reason, and a kind from:
   spelling | grammar | punctuation | word-choice | style.

Return ONLY a JSON array, at most 40 items, like:
[{"original": "their is", "replacement": "there is", "reason": "'There' introduces existence; 'their' is possessive.", "kind": "grammar"}]
Return [] if nothing needs fixing.

TEXT:
\"\"\"
%s
\"\"\""""


async def _refine_with_gemini(text: str, issues: list[dict], gemini_key: str | None = None) -> list[dict] | None:
    """Ask Gemini to validate LanguageTool's proposals and add misses. Returns None on failure."""
    snippet = text[:12000]
    proposals = [
        {"original": text[i["offset"] : i["offset"] + i["length"]], "replacement": (i["replacements"] or [""])[0], "message": i["message"][:120]}
        for i in issues[:40]
    ]
    data = await llm.ask_json(_REVIEW_PROMPT % (json.dumps(proposals, ensure_ascii=False), snippet), api_key=gemini_key)
    if not isinstance(data, list):
        return None
    out: list[dict] = []
    cursor = 0
    used: set[tuple[int, int]] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        orig = str(item.get("original") or "").strip()
        rep = str(item.get("replacement") or "").strip()
        if not orig or orig == rep or len(orig) > 120:
            continue
        # locate the original: prefer the first occurrence after the previous match, else anywhere
        pos = text.find(orig, cursor)
        if pos < 0:
            pos = text.find(orig)
        if pos < 0:
            continue
        span = (pos, pos + len(orig))
        if span in used:
            continue
        used.add(span)
        cursor = pos + len(orig)
        kind = str(item.get("kind") or "grammar").lower()
        if kind not in {"spelling", "grammar", "punctuation", "word-choice", "style"}:
            kind = "grammar"
        out.append(
            {
                "offset": pos,
                "length": len(orig),
                "message": str(item.get("reason") or "Suggested correction")[:300],
                "short": kind.replace("-", " ").title(),
                "kind": kind,
                "replacements": [rep] if rep else [],
                "rule": "GEMINI_REVIEW",
                "context": "",
            }
        )
    out.sort(key=lambda i: i["offset"])
    return out


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
    reps0 = [r["value"] for r in m.get("replacements", [])[:3]]
    # "pgvector -> pg vector", "datasets -> data sets": same letters, only spacing/hyphens differ -> product/tech term
    if any(re.sub(r"[\s-]", "", r).lower() == re.sub(r"[\s-]", "", word).lower() for r in reps0):
        return True
    # lowercase single token with no vowels or with internal digits/underscores is an identifier, not a typo
    if re.fullmatch(r"[a-z0-9_]{3,}", word) and (not re.search(r"[aeiou]", word) or "_" in word):
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
