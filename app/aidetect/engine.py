"""AI-content detection ensemble.

    p_final = weighted blend of
        Binoculars score   (strongest signal, zero-shot, model pair)
        Perplexity         (classic GPTZero signal)
        Stylometry         (explainable, model-free)
        Gemini judge       (optional, bounded weight; the user's own key)

Honesty rules baked in here:
  * English only. The language models cannot judge other languages, so we return no score at all
    rather than a meaningless one.
  * Conservative verdicts. A false "AI-generated" label can hurt a student far more than a miss,
    so the label needs p >= 0.70 and short texts never get a confident verdict.
  * Calibration is small. The constants below come from the 10 bundled samples (see
    tests/calibrate.py); treat every score as evidence to investigate, never as proof.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from ..textproc import Sentence, english_score, is_english, split_sentences
from . import llm_judge
from .lm import SCORER, span_stats
from .stylometry import features, style_score

# ---- calibration (distilgpt2 observer / gpt2 performer) ------------------------------
# Binoculars score at which p = 0.5 (lower = more AI-like). Measured on samples/:
# AI 0.68-0.75, human 0.79-0.96 -> midpoint 0.775.
BINO_MID = 0.775
BINO_SCALE = 0.035
# Observer log-perplexity (nats) at which p = 0.5. Measured on samples/ with distilgpt2:
# AI texts 2.9-3.6, human texts 4.0-5.8  ->  midpoint 3.8.
PPL_MID = 3.8
PPL_SCALE = 0.35
SENT_BINO_MID = 0.80
SENT_BINO_SCALE = 0.08

W_BINO, W_PPL, W_STYLE, W_LLM = 0.50, 0.15, 0.35, 0.30
MIN_WORDS_CONFIDENT = 120
AI_VERDICT_AT = 0.70      # conservative on purpose
HUMAN_VERDICT_AT = 0.35


@dataclass
class AIResult:
    ai_probability: float | None      # 0..100, or None when no honest score can be given
    verdict: str
    confidence: str                   # none | low | medium | high
    signals: dict
    reasons: list[str]
    sentences: list[dict]             # per-sentence ai probability for highlighting
    stylometry: dict
    llm_judge: dict | None
    model: dict
    elapsed: float
    supported: bool = True
    warnings: list[str] = field(default_factory=list)


def _sig(x: float, mid: float, scale: float, invert: bool = False) -> float:
    z = (mid - x) / scale if invert else (x - mid) / scale
    return 1 / (1 + math.exp(-max(-60.0, min(60.0, z))))


async def detect_ai(text: str, *, use_llm_judge: bool = True, sentences: list[Sentence] | None = None, gemini_key: str | None = None) -> AIResult:
    t0 = time.perf_counter()
    warnings: list[str] = []
    sents = sentences if sentences is not None else split_sentences(text)
    body_sents = [s for s in sents if not s.is_reference]
    all_words = [w for s in body_sents for w in s.words]

    # ---- language gate: the models are English-only
    if not is_english(all_words):
        feats = features(text, body_sents)
        return AIResult(
            ai_probability=None,
            verdict="Not available for this language",
            confidence="none",
            signals={"english_score": round(english_score(all_words), 3)},
            reasons=[],
            sentences=[{"index": s.index, "start": s.start, "end": s.end, "p": None} for s in body_sents],
            stylometry=feats,
            llm_judge=None,
            model=SCORER.info(),
            elapsed=round(time.perf_counter() - t0, 2),
            supported=False,
            warnings=["AI detection currently works for English text only. This text does not look like English, so no AI score is shown rather than a misleading one."],
        )

    # Run LM scoring (CPU/GPU bound) and the Gemini judge (network) concurrently.
    judge_task = asyncio.create_task(llm_judge.judge(text, gemini_key)) if use_llm_judge and llm_judge.available(gemini_key) else None
    stats = await asyncio.to_thread(SCORER.score, text)
    judge = await judge_task if judge_task else None

    feats = features(text, body_sents)
    p_style, reasons = style_score(feats)
    signals: dict = {"stylometry_p": round(p_style, 3)}

    parts: list[tuple[float, float]] = [(W_STYLE, p_style)]
    if stats is not None:
        p_ppl = _sig(stats.log_ppl, PPL_MID, PPL_SCALE, invert=True)
        signals["log_perplexity"] = round(stats.log_ppl, 3)
        signals["perplexity"] = round(stats.ppl, 2)
        signals["perplexity_p"] = round(p_ppl, 3)
        parts.append((W_PPL, p_ppl))
        if stats.binoculars is not None:
            p_bino = _sig(stats.binoculars, BINO_MID, BINO_SCALE, invert=True)
            signals["binoculars"] = round(stats.binoculars, 4)
            signals["binoculars_p"] = round(p_bino, 3)
            parts.append((W_BINO, p_bino))
            if p_bino > 0.75:
                reasons.insert(0, "Token predictability pattern (Binoculars) strongly resembles LLM output")
            elif p_bino < 0.25:
                reasons.insert(0, "Token predictability pattern (Binoculars) resembles human writing")
        else:
            parts.append((W_BINO * 0.8, p_ppl))  # perplexity-only mode: perplexity carries the LM signal
            warnings.append("Binoculars disabled (performer model not loaded); using perplexity only.")
        if stats.ppl < 8 and feats["words"] > 60:
            reasons.append("Very low perplexity: text is extremely predictable to the language model")
        if stats.chunks < stats.chunks_total:
            warnings.append(f"Long document: AI detection sampled {stats.chunks} of {stats.chunks_total} sections spread across the text.")
    else:
        warnings.append("Language models are unavailable right now; this result uses writing-style signals only and is much less reliable.")

    if judge and "probability" in judge:
        signals["gemini_p"] = round(judge["probability"], 3)
        parts.append((W_LLM, judge["probability"]))
        reasons.extend(f"Gemini: {r}" for r in judge.get("reasons", [])[:2])
    elif judge and "error" in judge:
        warnings.append("Gemini could not give a second opinion this time (key quota or network).")

    wsum = sum(w for w, _ in parts)
    p = sum(w * v for w, v in parts) / wsum

    # ---- per-sentence scores
    sent_rows = []
    for s in body_sents:
        row = {"index": s.index, "start": s.start, "end": s.end, "p": None}
        if stats is not None and s.word_count >= 5:
            ss = span_stats(stats, s.start, s.end)
            if ss:
                if "binoculars" in ss:
                    sp = _sig(ss["binoculars"], SENT_BINO_MID, SENT_BINO_SCALE, invert=True)
                else:
                    sp = _sig(ss["log_ppl"], PPL_MID, PPL_SCALE, invert=True)
                # shrink short-sentence estimates toward the document score
                k = min(1.0, ss["tokens"] / 40)
                row["p"] = round(k * sp + (1 - k) * p, 3)
                row["ppl"] = round(math.exp(min(20.0, ss["log_ppl"])), 1)
        sent_rows.append(row)

    # burstiness: spread of per-sentence perplexity (shown as a signal only; it did not separate
    # human from AI on the bundled samples, so it does not influence the score)
    ppls = [r["ppl"] for r in sent_rows if r.get("ppl")]
    if len(ppls) >= 5:
        arr = np.log(np.array(ppls))
        signals["burstiness"] = round(float(arr.std() / max(1e-6, arr.mean())), 3)

    words = feats["words"]
    lm_ok = stats is not None
    if words < 50:
        confidence = "low"
        warnings.append("Fewer than 50 words: AI detection is unreliable on very short text.")
    elif not lm_ok or words < MIN_WORDS_CONFIDENT or abs(p - 0.5) < 0.15:
        confidence = "medium" if lm_ok and words >= 50 and abs(p - 0.5) >= 0.15 else "low"
    else:
        confidence = "high" if abs(p - 0.5) > 0.3 else "medium"

    if p >= AI_VERDICT_AT and confidence != "low":
        verdict = "Likely AI-generated"
    elif p <= HUMAN_VERDICT_AT:
        verdict = "Likely human-written"
    else:
        verdict = "Mixed / uncertain"

    return AIResult(
        ai_probability=round(100 * p, 1),
        verdict=verdict,
        confidence=confidence,
        signals=signals,
        reasons=reasons[:8],
        sentences=sent_rows,
        stylometry=feats,
        llm_judge=judge,
        model=SCORER.info(),
        elapsed=round(time.perf_counter() - t0, 2),
        warnings=warnings,
    )


def to_dict(r: AIResult) -> dict:
    return asdict(r)
