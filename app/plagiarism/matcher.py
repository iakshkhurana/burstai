"""Compare the submitted document against candidate source texts.

Three complementary detectors, each catching what the others miss:
  1. Shared long word runs            -> verbatim copying, even across sentence boundaries
  2. Fuzzy token-set ratio (rapidfuzz) -> light edits, reordering, word swaps
  3. Sentence embeddings (MiniLM)     -> paraphrase / "humanized" rewrites

Cost control: fuzzy matching runs in C++ over every sentence pair, but embeddings are computed only
for the handful of source sentences per page that are lexically closest to the document, and for all
pages in a single batch. That keeps a scan with dozens of long pages to a few seconds on a CPU.
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

EXACT_FUZZ = 88        # token_set_ratio >= this  -> "exact" (verbatim or trivially edited)
NEAR_FUZZ = 72         # token_set_ratio >= this  -> "near"  (light rewrite)
PARAPHRASE_COS = 0.82  # cosine >= this (with fuzzy below NEAR) -> "paraphrase"
PARAPHRASE_MIN_FUZZ = 30   # a paraphrase must still share some vocabulary with its source
SHARED_NGRAM = 8       # a shared run of >= 8 words is treated as verbatim
MAX_SOURCE_SENTS = 1500
MAX_SOURCE_CHARS = 250_000
EMBED_PER_SOURCE = 48  # embed only the lexically closest sentences of each page
EMBED_BUDGET = 1600    # hard cap on source sentences embedded per scan


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
            if config.OFFLINE_LM:
                _embedder_failed = True
                return None
            try:
                from sentence_transformers import SentenceTransformer

                _embedder = SentenceTransformer(config.EMBED_MODEL, device=_device())
                log.info("loaded sentence embedder")
            except Exception as e:  # noqa: BLE001
                log.warning("sentence embedder unavailable (%s); paraphrase detection disabled", type(e).__name__)
                _embedder_failed = True
    return _embedder


def embedder_ready() -> bool:
    """Non-blocking status check (never triggers a load)."""
    return _embedder is not None


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
    with _embed_lock:  # one encode at a time; parallel encodes only fight over the same cores
        vecs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(vecs, dtype=np.float32)


# ----------------------------------------------------------------------------- matching
def quick_overlap(doc_fp: Fingerprint, source_text: str) -> float:
    return overlap(doc_fp, winnow(tokenize_words(source_text[:MAX_SOURCE_CHARS])))


def match_all(doc_sents: list[Sentence], doc_vecs: np.ndarray | None, sources: dict[str, str]) -> list[SentenceMatch]:
    """Match every document sentence against every source. Returns all candidate matches;
    the caller keeps the best one per sentence."""
    if not doc_sents or not sources:
        return []
    doc_texts = [s.text for s in doc_sents]
    doc_ngrams = [ngrams(s.words, SHARED_NGRAM) if s.word_count >= SHARED_NGRAM else set() for s in doc_sents]

    # ---- pass 1 (cheap, per source): fuzzy matrix, long shared runs, and which sentences deserve an embedding
    prepared = []
    to_embed: list[str] = []
    for url, text in sources.items():
        text = text[:MAX_SOURCE_CHARS]
        src_texts = [s.text for s in split_sentences(text) if s.word_count >= 4][:MAX_SOURCE_SENTS]
        if not src_texts:
            continue
        sim = np.asarray(
            process.cdist(doc_texts, src_texts, scorer=fuzz.token_set_ratio, score_cutoff=PARAPHRASE_MIN_FUZZ, workers=-1), dtype=np.float32
        )
        src_ngrams = ngrams(tokenize_words(text), SHARED_NGRAM)
        cols: list[int] = []
        if doc_vecs is not None and len(to_embed) < EMBED_BUDGET:
            best = sim.max(axis=0)
            order = np.argsort(-best)[:EMBED_PER_SOURCE]
            cols = [int(j) for j in order if best[j] >= PARAPHRASE_MIN_FUZZ][: EMBED_BUDGET - len(to_embed)]
        prepared.append((url, src_texts, sim, src_ngrams, cols, len(to_embed)))
        to_embed.extend(src_texts[j] for j in cols)

    # ---- pass 2: one batched embedding call for every shortlisted source sentence
    src_vecs = embed(to_embed) if to_embed else None

    # ---- pass 3: decide
    out: list[SentenceMatch] = []
    for url, src_texts, sim, src_ngrams, cols, offset in prepared:
        cos = doc_vecs @ src_vecs[offset : offset + len(cols)].T if (src_vecs is not None and cols) else None
        for i, s in enumerate(doc_sents):
            j = int(sim[i].argmax())
            fz = float(sim[i, j])
            has_run = bool(doc_ngrams[i] & src_ngrams)
            if fz >= EXACT_FUZZ or has_run:
                out.append(SentenceMatch(i, "exact", max(fz, 90.0) if has_run else fz, url, src_texts[j]))
                continue
            if fz >= NEAR_FUZZ and s.word_count >= 6:
                out.append(SentenceMatch(i, "near", fz, url, src_texts[j]))
                continue
            if cos is not None and s.word_count >= 8:
                k = int(cos[i].argmax())
                c = float(cos[i, k])
                # generic sentences embed close to everything, so also require shared vocabulary
                if c >= PARAPHRASE_COS and float(sim[i, cols[k]]) >= PARAPHRASE_MIN_FUZZ:
                    out.append(SentenceMatch(i, "paraphrase", round(c * 100, 1), url, src_texts[cols[k]]))
    return out


def match_sentences(doc_sents: list[Sentence], doc_vecs: np.ndarray | None, source_url: str, source_text: str) -> list[SentenceMatch]:
    """Single-source convenience wrapper around match_all."""
    return match_all(doc_sents, doc_vecs, {source_url: source_text})
