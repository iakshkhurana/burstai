"""Optional Gemini "brain". Used when a key is available: the user's own key sent with the request
(X-Gemini-Key header / options.gemini_key, kept in their browser) or GEMINI_API_KEY in .env.

Everything here is failure-safe: any error returns None and callers fall back to the
rule-based result, so the app never depends on the key. Keys are never logged or stored.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from . import config

log = logging.getLogger(__name__)
_clients: dict[str, object] = {}


def resolve_key(key: str | None) -> str:
    return (key or "").strip() or config.GEMINI_API_KEY


def available(key: str | None = None) -> bool:
    return bool(resolve_key(key))


def _client(key: str):
    h = hashlib.sha256(key.encode()).hexdigest()
    c = _clients.get(h)
    if c is None:
        from google import genai

        if len(_clients) > 64:
            _clients.clear()
        c = _clients[h] = genai.Client(api_key=key)
    return c


async def ask_json(prompt: str, *, api_key: str | None = None, timeout: float = 40.0, temperature: float = 0.0):
    """Send a prompt that asks for JSON; return the parsed object or None."""
    key = resolve_key(api_key)
    if not key:
        return None
    try:
        resp = await asyncio.wait_for(
            _client(key).aio.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config={"temperature": temperature, "response_mime_type": "application/json"},
            ),
            timeout=timeout,
        )
        raw = (resp.text or "").strip()
        m = re.search(r"[\[{].*[\]}]", raw, re.S)
        return json.loads(m.group(0) if m else raw)
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini call failed: %s", type(e).__name__)
        return None


async def validate(key: str) -> dict:
    """Cheap round-trip to confirm a key works. Returns {"ok": bool, "model": ..., "error": ...}."""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "error": "No key given"}
    try:
        resp = await asyncio.wait_for(
            _client(key).aio.models.generate_content(model=config.GEMINI_MODEL, contents="Reply with the single word OK."),
            timeout=25,
        )
        return {"ok": True, "model": config.GEMINI_MODEL, "reply": (resp.text or "").strip()[:20]}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        friendly = "Invalid key" if "API_KEY_INVALID" in msg or "401" in msg or "403" in msg else "Gemini rejected the request"
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            friendly = "Key works but the free quota is exhausted right now"
        return {"ok": False, "error": friendly}
