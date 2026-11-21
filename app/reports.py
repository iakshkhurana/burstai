"""Saved originality reports so a verification link (/v/<id>) works for anyone with access to this server."""
from __future__ import annotations

import json
import re
import time

from . import config

_DIR = config.DATA_DIR / "reports"
_DIR.mkdir(parents=True, exist_ok=True)
_ID = re.compile(r"^[A-Za-z0-9-]{4,32}$")


def save(report: dict) -> str:
    rid = str(report.get("id") or "").strip()
    if not _ID.match(rid):
        raise ValueError("bad report id")
    report = dict(report)
    report["saved_at"] = time.time()
    report.pop("text", None)  # never persist the full essay; the report is enough to verify scores
    (_DIR / f"{rid}.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return rid


def load(rid: str) -> dict | None:
    if not _ID.match(rid):
        return None
    p = _DIR / f"{rid}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
