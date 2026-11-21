"""Hand-crafted stylometric features that separate typical LLM prose from typical human prose.

None of these is decisive alone (and all can be gamed by a "humanizer"), but together they
are a cheap, model-free second opinion and they make the report *explainable*.
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter

from ..textproc import Sentence, tokenize_words

# Phrases heavily over-represented in ChatGPT/Gemini/Claude output relative to human web text.
LLM_PHRASES = [
    "delve", "delves", "delving", "tapestry", "testament to", "pivotal", "crucial role", "plays a crucial",
    "it is important to note", "it's important to note", "it is worth noting", "in today's fast-paced",
    "in the realm of", "in the ever-evolving", "ever-evolving", "landscape of", "navigate the", "navigating the",
    "underscore", "underscores", "underscoring", "multifaceted", "fostering", "foster a", "leverage", "leveraging",
    "a myriad of", "myriad", "paramount", "furthermore", "moreover", "additionally", "in conclusion",
    "overall,", "ultimately,", "notably,", "importantly,", "significantly,", "holistic", "robust", "seamless",
    "seamlessly", "comprehensive", "transformative", "game-changer", "revolutionize", "harness the power",
    "unlock the", "embark on", "embarking on", "journey", "beacon", "intricate", "intricacies", "nuanced",
    "shed light on", "sheds light", "a double-edged sword", "the advent of", "stands as", "serves as a",
    "not only", "but also", "whether you're", "look no further", "dive into", "deep dive", "at its core",
    "in summary", "to sum up", "it is essential", "it is crucial", "cannot be overstated", "plays a vital role",
    "vibrant", "bustling", "enhance", "enhancing", "streamline", "optimal", "utilize", "utilizing", "facilitate",
    "elevate", "empower", "empowering", "cutting-edge", "state-of-the-art", "groundbreaking", "unprecedented",
    "meticulous", "meticulously", "realm", "paradigm", "synergy", "spearhead", "pave the way", "cornerstone",
]
_PHRASE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(LLM_PHRASES, key=len, reverse=True)) + r")\b", re.I)

_HUMAN_MARKERS = re.compile(
    r"\b(i|i'm|i've|i'd|i'll|me|my|mine|we|we're|our|lol|lmao|haha|tbh|idk|imo|btw|gonna|wanna|kinda|sorta|"
    r"honestly|literally|basically|actually|like|y'know|yeah|yep|nope|ok|okay|damn|hell|ugh|wow|hmm|anyway|anyways)\b",
    re.I,
)
_CONTRACTION = re.compile(r"\b\w+'(s|t|re|ve|ll|d|m)\b", re.I)
_LIST_MARK = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_HEADING_COLON = re.compile(r"^\s*[A-Z][^\n]{2,60}:\s*$", re.M)


def features(text: str, sentences: list[Sentence]) -> dict:
    words = tokenize_words(text)
    n = max(1, len(words))
    sent_lens = [s.word_count for s in sentences if s.word_count > 0] or [n]
    mean_len = sum(sent_lens) / len(sent_lens)
    var = sum((x - mean_len) ** 2 for x in sent_lens) / len(sent_lens)
    std_len = math.sqrt(var)

    counts = Counter(words)
    ttr = len(counts) / n
    mattr = _mattr(words, 50)
    hapax = sum(1 for c in counts.values() if c == 1) / n
    avg_word_len = sum(len(w) for w in words) / n

    llm_hits = _PHRASE_RE.findall(text)
    human_hits = _HUMAN_MARKERS.findall(text)
    contractions = len(_CONTRACTION.findall(text))

    punct = Counter(ch for ch in text if ch in ",;:!?()\"'-—–")
    em_dash = text.count("—") + text.count(" - ")
    semicolons = punct[";"]
    exclam = punct["!"]
    questions = punct["?"]
    parens = punct["("]

    comp_ratio = len(zlib.compress(text.encode("utf-8"), 9)) / max(1, len(text.encode("utf-8")))

    # sentence starters: LLMs love starting with transitional adverbs / "This"/"These"
    starters = Counter((s.words[0] if s.words else "") for s in sentences)
    transitional = sum(
        starters[w] for w in ("however", "moreover", "furthermore", "additionally", "overall", "ultimately", "this", "these", "while", "by", "in", "as")
    ) / max(1, len(sentences))

    # repeated sentence-initial bigrams -> template-like structure
    starter_bigrams = Counter(" ".join(s.words[:2]) for s in sentences if len(s.words) >= 2)
    repeat_starts = sum(c - 1 for c in starter_bigrams.values() if c > 1) / max(1, len(sentences))

    # structural tells: lots of bullet lists / "Heading:" lines
    bullets = len(_LIST_MARK.findall(text)) / max(1, len(sentences))
    headings = len(_HEADING_COLON.findall(text)) / max(1, len(sentences))

    # extremely regular sentence lengths (low coefficient of variation) is a strong LLM tell
    cv_len = std_len / mean_len if mean_len else 0.0

    # typos / odd tokens: proportion of words with digits-letters mix or 3+ repeated chars (humans do this)
    weird = sum(1 for w in words if re.search(r"(.)\1\1", w)) / n

    return {
        "words": len(words),
        "sentences": len(sentences),
        "mean_sentence_len": round(mean_len, 2),
        "sentence_len_cv": round(cv_len, 3),           # "burstiness" in length
        "type_token_ratio": round(ttr, 3),
        "mattr50": round(mattr, 3),
        "hapax_ratio": round(hapax, 3),
        "avg_word_len": round(avg_word_len, 2),
        "llm_phrase_rate": round(1000 * len(llm_hits) / n, 2),   # per 1000 words
        "llm_phrases": sorted(set(h.lower() for h in llm_hits))[:25],
        "human_marker_rate": round(1000 * len(human_hits) / n, 2),
        "contraction_rate": round(1000 * contractions / n, 2),
        "em_dash_rate": round(1000 * em_dash / n, 2),
        "semicolon_rate": round(1000 * semicolons / n, 2),
        "exclamation_rate": round(1000 * exclam / n, 2),
        "question_rate": round(1000 * questions / n, 2),
        "paren_rate": round(1000 * parens / n, 2),
        "compression_ratio": round(comp_ratio, 3),
        "transitional_starter_rate": round(transitional, 3),
        "repeated_starter_rate": round(repeat_starts, 3),
        "bullet_rate": round(bullets, 3),
        "heading_rate": round(headings, 3),
        "weird_token_rate": round(1000 * weird / n, 2),
    }


def _mattr(words: list[str], window: int) -> float:
    """Moving-average type-token ratio (length independent, unlike raw TTR)."""
    if len(words) <= window:
        return len(set(words)) / max(1, len(words))
    total = 0.0
    counts = Counter(words[:window])
    total += len(counts) / window
    for i in range(window, len(words)):
        out_w, in_w = words[i - window], words[i]
        counts[out_w] -= 1
        if counts[out_w] == 0:
            del counts[out_w]
        counts[in_w] += 1
        total += len(counts) / window
    return total / (len(words) - window + 1)


def style_score(f: dict) -> tuple[float, list[str]]:
    """Map features to a 0..1 'looks like AI' probability plus human-readable reasons.

    Weights are hand-set from the published literature on LLM stylometry and sanity-checked on
    the bundled samples - not a trained classifier. Deliberately conservative: most features
    pull only when they are far from the human norm.
    """
    z = 0.0
    reasons: list[str] = []

    r = f["llm_phrase_rate"]  # human web text ~1-4 / 1000 words; ChatGPT essays often 8-25
    if r >= 10:
        z += 1.4; reasons.append(f"Very high density of LLM-favourite phrases ({r:.0f}/1000 words)")
    elif r >= 5:
        z += 0.6; reasons.append(f"Elevated density of LLM-favourite phrases ({r:.0f}/1000 words)")
    elif r <= 1.5 and f["words"] > 150:
        z -= 0.4

    cv = f["sentence_len_cv"]  # humans ~0.5-0.7; LLM essays often 0.25-0.4
    if f["sentences"] >= 6:
        if cv < 0.32:
            z += 1.0; reasons.append("Sentence lengths are unusually uniform (low burstiness)")
        elif cv < 0.42:
            z += 0.4; reasons.append("Sentence lengths are fairly uniform")
        elif cv > 0.65:
            z -= 0.5

    if f["human_marker_rate"] + f["contraction_rate"] < 3 and f["words"] > 120:
        z += 0.5; reasons.append("No first-person voice, contractions or informal markers")
    elif f["human_marker_rate"] + f["contraction_rate"] > 20:
        z -= 0.8

    if f["transitional_starter_rate"] > 0.45 and f["sentences"] >= 6:
        z += 0.5; reasons.append("Many sentences open with transitional words (However, Moreover, This...)")
    if f["repeated_starter_rate"] > 0.25 and f["sentences"] >= 8:
        z += 0.3; reasons.append("Template-like repeated sentence openings")
    if f["bullet_rate"] > 0.3 or f["heading_rate"] > 0.15:
        z += 0.3; reasons.append("Heavy use of bullet lists / 'Heading:' structure")

    if f["weird_token_rate"] > 2 or f["exclamation_rate"] > 8:
        z -= 0.4
    if f["mattr50"] > 0.86 and f["words"] > 200:
        z -= 0.3  # unusually rich vocabulary per window is more typical of skilled humans

    p = 1 / (1 + math.exp(-(z - 0.6)))
    return p, reasons
