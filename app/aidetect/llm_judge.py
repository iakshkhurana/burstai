"""Optional third opinion from Gemini (user's own key or GEMINI_API_KEY).

The judge returns a probability and short reasons; the ensemble gives it a bounded weight because
LLM judges are confident but not reliably calibrated.
"""
from __future__ import annotations

from .. import config, llm

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


def available(key: str | None = None) -> bool:
    return llm.available(key)


async def judge(text: str, api_key: str | None = None) -> dict | None:
    if not llm.available(api_key):
        return None
    data = await llm.ask_json(_PROMPT % text[:12000], api_key=api_key)
    if not isinstance(data, dict):
        return {"error": "Gemini did not answer"}
    try:
        p = float(data.get("ai_probability", 50)) / 100
    except (TypeError, ValueError):
        p = 0.5
    return {"probability": max(0.0, min(1.0, p)), "reasons": [str(r) for r in data.get("reasons", [])][:5], "model": config.GEMINI_MODEL}
