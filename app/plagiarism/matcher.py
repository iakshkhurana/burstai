"""Compare the submitted document against candidate source texts.

Three complementary detectors, each catching what the others miss:
  1. Winnowing fingerprints          -> verbatim copying, fast, document level
  2. Fuzzy token-set ratio (rapidfuzz) -> light edits, reordering, word swaps
  3. Sentence embeddings (MiniLM)     -> paraphrase / "humanized" rewrites
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
from rapidfuzz import fuzz, process

from .. import config
from ..fingerprint import Fingerprint, ngrams, overlap, winnow
from ..textproc import Sentence, split_sentences, tokenize_words

log = logging.getLogger(__name__)

EXACT_FUZZ = 88      # token_set_ratio >= this  -> "exact" (verbatim or trivially edited)
NEAR_FUZZ = 72       # token_set_ratio >= this  -> "near"  (light rewrite)
PARAPHRASE_COS = 0.82  # cosine >= this (and fuzzy below NEAR) -> "paraphrase"
SHARED_NGRAM = 8     # a shared run of >= 8 words is treated as verbatim


@dataclass
class SentenceMatch:
    sentence_index: int
    kind: str            # exact | near | paraphrase
    score: float         # 0..100
    source_url: str
    source_text: str


# ----------------------------------------------------------------------------- embeddings
_embed_lock = threading.Lock()
_embedder = None
_embedder_failed = False


def get_embedder():
    """Lazy-load the sentence-transformer. Returns None if unavailable (we degrade gracefully)."""
    global _embedder, _embedder_failed
    if _embedder is not None or _embedder_failed:
        return _embedder
    with _embed_lock:
        if _embedder is None and not _embedder_failed:
            try:
                from sentence_transformers import SentenceTransformer

                _embedder = SentenceTransformer(config.EMBED_MODEL, device=_device())
                log.info("loaded embedder %s", config.EMBED_MODEL)
            except Exception as e:  # noqa: BLE001
                log.warning("sentence embedder unavailable (%s); paraphrase detection disabled", e)
                _embedder_failed = True
    return _embedder


def _device() -> str:
    if config.DEVICE != "auto":
        return config.DEVICE
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def embed(texts: list[str]) -> np.ndarray | None:
    model = get_embedder()
    if model is None or not texts:
        return None
    vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


# ----------------------------------------------------------------------------- matching
def quick_overlap(doc_fp: Fingerprint, source_text: str) -> float:
    return overlap(doc_fp, winnow(tokenize_words(source_text)))


def match_sentences(
    doc_sents: list[Sentence],
    doc_vecs: np.ndarray | None,
    source_url: str,
    source_text: str,
    max_source_sents: int = 1500,
) -> list[SentenceMatch]:
    src_sents = [s for s in split_sentences(source_text) if s.word_count >= 4][:max_source_sents]
    if not src_sents or not doc_sents:
        return []

    doc_texts = [s.text for s in doc_sents]
    src_texts = [s.text for s in src_sents]

    # 1) fuzzy similarity matrix in C++ (rows = doc sentences, cols = source sentences)
    sim = process.cdist(doc_texts, src_texts, scorer=fuzz.token_set_ratio, score_cutoff=NEAR_FUZZ - 10, workers=-1)
    sim = np.asarray(sim, dtype=np.float32)

    # 2) shared long n-grams catch verbatim runs that straddle sentence boundaries
    src_ngrams = ngrams(tokenize_words(source_text), SHARED_NGRAM)

    # 3) semantic similarity for paraphrase
    cos = None
    if doc_vecs is not None:
        src_vecs = embed(src_texts)
        if src_vecs is not None:
            cos = doc_vecs @ src_vecs.T

    out: list[SentenceMatch] = []
    for i, s in enumerate(doc_sents):
        j = int(sim[i].argmax())
        fz = float(sim[i, j])
        has_run = s.word_count >= SHARED_NGRAM and bool(ngrams(s.words, SHARED_NGRAM) & src_ngrams)

        if fz >= EXACT_FUZZ or has_run:
            out.append(SentenceMatch(i, "exact", max(fz, 90.0 if has_run else fz), source_url, src_texts[j]))
            continue
        if fz >= NEAR_FUZZ and s.word_count >= 6:
            out.append(SentenceMatch(i, "near", fz, source_url, src_texts[j]))
            continue
        if cos is not None and s.word_count >= 8:
            k = int(cos[i].argmax())
            c = float(cos[i, k])
            # very short / generic sentences embed close to everything; require some lexical overlap too
            if c >= PARAPHRASE_COS and float(sim[i, k]) >= 35:
                out.append(SentenceMatch(i, "paraphrase", round(c * 100, 1), source_url, src_texts[k]))
    return out
