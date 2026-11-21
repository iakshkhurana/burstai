"""Optional third opinion from Gemini's free tier.

Only used when GEMINI_API_KEY is set. The judge returns a probability and short reasons; the
ensemble gives it a bounded weight because LLM judges are confident but not reliably calibrated.
"""
from __future__ import annotations

import json
import logging
import re

from .. import config

log = logging.getLogger(__name__)

_PROMPT = """You are an expert forensic linguist. Decide whether the TEXT below was written by a
large language model (ChatGPT, Gemini, Claude, Llama...) or by a human.

Consider: lexical predictability, sentence-length variation, generic vs. specific detail,
hedging and transitional phrases, personal voice, errors/typos, formatting habits.
Humans can write formally and LLMs can be told to write casually, so weigh *specific,
verifiable, idiosyncratic* detail heavily toward human.

Respond with ONLY a JSON object: {"ai_probability": <0-100 integer>, "reasons": ["...", "..."]}

TEXT:
\"\"\"
%s
\"\"\""""


def available() -> bool:
    return bool(config.GEMINI_API_KEY)


async def judge(text: str) -> dict | None:
    if not available():
        return None
    try:
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = await client.aio.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=_PROMPT % text[:12000],
            config={"temperature": 0.0, "response_mime_type": "application/json"},
        )
        raw = resp.text or ""
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        p = float(data.get("ai_probability", 50)) / 100
        return {"probability": max(0.0, min(1.0, p)), "reasons": [str(r) for r in data.get("reasons", [])][:5], "model": config.GEMINI_MODEL}
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini judge failed: %s", e)
        return {"error": str(e)}
