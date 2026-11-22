"""Optional Gemini "brain". Used only when GEMINI_API_KEY is set in .env (the user's own key).

Everything here is failure-safe: any error returns None and callers fall back to the
rule-based result, so the app never depends on the key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from . import config

log = logging.getLogger(__name__)
_client = None


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


def _get_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


async def ask_json(prompt: str, *, timeout: float = 40.0, temperature: float = 0.0):
    """Send a prompt that asks for JSON; return the parsed object or None."""
    if not available():
        return None
    try:
        client = _get_client()
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
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
        log.warning("Gemini call failed: %s", str(e)[:200])
        return None
