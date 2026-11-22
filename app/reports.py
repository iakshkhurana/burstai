"""Public verification copies of reports (/v/<id>) and sanitising of report views.

A verification copy contains ONLY what the engine itself signed when it produced the result
(scores, source list, a hash of the checked text, timestamp) plus a display title. Nothing a browser
sends is trusted: the signature must verify, and the id is derived from the signature, so a report
can be neither forged nor overwritten. No essay text is ever stored.
"""
from __future__ import annotations

import json
import re
import time

from . import config
from .security import report_id, verify_attestation

_DIR = config.DATA_DIR / "reports"
_DIR.mkdir(parents=True, exist_ok=True)
_ID = re.compile(r"^BA-[0-9A-F]{5}-[0-9A-F]{5}$")
_KINDS = {"exact", "near", "paraphrase"}


def _clean(s, n: int) -> str:
    return re.sub(r"[\x00-\x1f<>]", " ", str(s or ""))[:n].strip()


def save(body: dict) -> str:
    payload, sig = body.get("payload"), body.get("sig")
    if not verify_attestation(payload, sig):
        raise ValueError("This result was not signed by this engine, so it cannot be certified. Run the scan again.")
    rid = report_id(sig)
    doc = {"id": rid, "payload": payload, "sig": sig, "title": _clean(body.get("title"), 160) or "Untitled", "saved_at": time.time()}
    path = _DIR / f"{rid}.json"
    if not path.exists():  # idempotent; never overwrite an existing certificate
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return rid


def load(rid: str) -> dict | None:
    if not _ID.match(rid or ""):
        return None
    p = _DIR / f"{rid}.json"
    if not p.exists():
        return None
    doc = json.loads(p.read_text(encoding="utf-8"))
    return doc if verify_attestation(doc.get("payload"), doc.get("sig")) else None


# ----------------------------------------------------------------------------- account-saved views
def _num(v, lo: float, hi: float):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else max(lo, min(hi, f))


def _int(v, hi: int = 10**7) -> int:
    n = _num(v, 0, hi)
    return int(n) if n is not None else 0


def sanitize_view(r: dict) -> dict:
    """Allow-list the fields of a browser-built report before saving it to an account.
    Sentence text is dropped on purpose: saved scans keep scores and sources, never the essay."""
    if not isinstance(r, dict):
        raise ValueError("bad report")
    sources = []
    for s in (r.get("sources") or [])[:25]:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "")[:500]
        if not re.match(r"^(https?://|corpus://)", url):
            continue
        kind = s.get("mainKind") if s.get("mainKind") in _KINDS else "exact"
        sources.append({"n": _int(s.get("n"), 999), "url": url, "host": _clean(s.get("host"), 120), "title": _clean(s.get("title"), 200),
                        "kind": _clean(s.get("kind"), 40), "pct": _num(s.get("pct"), 0, 100) or 0.0, "words": _int(s.get("words")), "mainKind": kind})
    rows = []
    for row in (r.get("rows") or [])[:3000]:
        if not isinstance(row, dict):
            continue
        rows.append({"src": _int(row.get("src"), 999) or None, "ai": _num(row.get("ai"), 0, 1),
                     "excluded": _clean(row.get("excluded"), 20) or None, "kind": row.get("kind") if row.get("kind") in _KINDS else None})
    g = r.get("grammar")
    grammar = None
    if isinstance(g, dict):
        kinds = {(_clean(k, 20) or "other"): _int(v, 10**5) for k, v in list((g.get("kinds") or {}).items())[:12]}
        grammar = {"open": _int(g.get("open"), 10**5), "kinds": kinds}
    att = r.get("attestation")
    attestation = None
    if isinstance(att, dict) and verify_attestation(att.get("payload"), att.get("sig")):
        attestation = {"payload": att["payload"], "sig": att["sig"]}
    rid = str(r.get("id") or "")
    return {
        "id": rid if _ID.match(rid) else "",
        "date": _num(r.get("date"), 0, 1e14) or time.time() * 1000,
        "title": _clean(r.get("title"), 160) or "Untitled",
        "words": _int(r.get("words")),
        "similarity": _num(r.get("similarity"), 0, 100),
        "aiPct": _num(r.get("aiPct"), 0, 100),
        "aiLevel": _clean(r.get("aiLevel"), 20),
        "aiVerdict": _clean(r.get("aiVerdict"), 60),
        "aiConfidence": _clean(r.get("aiConfidence"), 20),
        "aiFlagged": _int(r.get("aiFlagged")),
        "aiTotal": _int(r.get("aiTotal")),
        "sources": sources,
        "rows": rows,
        "grammar": grammar,
        "hasPlag": bool(r.get("hasPlag")),
        "hasAi": bool(r.get("hasAi")),
        "hasGrammar": bool(r.get("hasGrammar")),
        "edited": bool(r.get("edited")),
        "attestation": attestation,
    }
