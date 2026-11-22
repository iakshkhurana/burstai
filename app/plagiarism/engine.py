"""Plagiarism pipeline: segment -> search -> fetch -> match -> report."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field

from .. import config
from ..fingerprint import winnow
from ..textproc import Sentence, split_sentences, tokenize_words
from . import corpus
from .fetch import fetch_all
from .matcher import SentenceMatch, embed, match_sentences, quick_overlap
from .search import Candidate, find_candidates

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    url: str
    title: str
    origin: str
    matched_words: int
    percent: float
    kinds: dict[str, int] = field(default_factory=dict)


@dataclass
class PlagiarismResult:
    similarity: float                 # overall % of (counted) words that match some source
    exact_percent: float
    near_percent: float
    paraphrase_percent: float
    total_words: int
    counted_words: int                # after exclusions
    sources: list[SourceReport]
    sentences: list[dict]             # per-sentence annotations for highlighting
    queries: list[str]
    candidates_checked: int
    elapsed: float
    warnings: list[str] = field(default_factory=list)
    # how many documents of each kind were actually compared; "web" == 0 means the free search
    # engines did not answer, so the score may be understated (surfaced in the UI and signed report)
    coverage: dict = field(default_factory=dict)


async def check_plagiarism(
    text: str,
    *,
    exclude_quotes: bool = True,
    exclude_references: bool = True,
    exclude_citations: bool = True,
    use_web: bool = True,
    use_corpus: bool = True,
    deep: bool = False,
    sentences: list[Sentence] | None = None,
    corpus_owner: str | None = None,
) -> PlagiarismResult:
    t0 = time.perf_counter()
    warnings: list[str] = []
    sents = sentences if sentences is not None else split_sentences(text)
    total_words = sum(s.word_count for s in sents)
    doc_fp = winnow(tokenize_words(text))

    # which sentences count toward the score
    def counted(s: Sentence) -> bool:
        if exclude_references and s.is_reference:
            return False
        if exclude_quotes and s.is_quote:
            return False
        if exclude_citations and s.is_citation:
            return False
        return True

    # ---- candidate retrieval
    cands: list[Candidate] = []
    queries: list[str] = []
    texts: dict[str, str] = {}
    titles: dict[str, str] = {}
    origins: dict[str, str] = {}

    status: dict = {}
    if use_web:
        try:
            cands, queries, status = await find_candidates(sents, deep=deep)
        except Exception as e:  # noqa: BLE001
            log.warning("search failed (%s)", type(e).__name__)
            warnings.append("Web search is unavailable right now, so online sources could not be checked.")
        if cands:
            texts = await fetch_all(cands)
            for c in cands:
                titles[c.url] = c.title or c.url
                origins[c.url] = c.origin
            if not texts:
                warnings.append("Found search results but could not download any page text.")
        n_web = sum(1 for u in texts if origins.get(u) == "web")
        if queries and status.get("web_blocked") and n_web == 0:
            warnings.append("Web search engines are refusing automated queries from this server right now, so ordinary web pages were NOT checked (Wikipedia and academic abstracts were). This similarity score may be understated and is not evidence of originality - scan again later.")
        elif queries and n_web == 0 and status.get("timed_out"):
            warnings.append("Web search did not finish in time, so ordinary web pages were NOT checked. This similarity score may be understated - scan again in a few minutes.")
        elif status.get("timed_out"):
            warnings.append("Some searches were still running when the time budget ended, so web coverage is partial.")

    if use_corpus and corpus_owner:
        for cid, title, ctext, _ov in corpus.candidates(doc_fp, corpus_owner):
            texts[cid] = ctext
            titles[cid] = title
            origins[cid] = "corpus"

    # cheap pre-filter: drop pages with zero fingerprint overlap AND tiny size
    keep = {}
    for url, src in texts.items():
        ov = quick_overlap(doc_fp, src)
        if ov > 0 or len(src) < 200_000:
            keep[url] = src
    texts = keep

    # ---- sentence-level matching (CPU-bound; run off the event loop)
    doc_vecs = await asyncio.to_thread(embed, [s.text for s in sents]) if sents else None
    all_matches: list[SentenceMatch] = []
    if texts:
        jobs = [asyncio.to_thread(match_sentences, sents, doc_vecs, url, src) for url, src in texts.items()]
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, Exception):
                log.warning("matcher failed: %s", res)
                continue
            all_matches.extend(res)

    # ---- best match per sentence (exact beats near beats paraphrase; then score)
    rank = {"exact": 3, "near": 2, "paraphrase": 1}
    best: dict[int, SentenceMatch] = {}
    for m in all_matches:
        cur = best.get(m.sentence_index)
        if cur is None or (rank[m.kind], m.score) > (rank[cur.kind], cur.score):
            best[m.sentence_index] = m

    # ---- aggregate
    per_source: dict[str, SourceReport] = {}
    kind_words = {"exact": 0, "near": 0, "paraphrase": 0}
    counted_words = 0
    ann = []
    for s in sents:
        is_counted = counted(s)
        if is_counted:
            counted_words += s.word_count
        m = best.get(s.index)
        entry = {
            "index": s.index,
            "start": s.start,
            "end": s.end,
            "words": s.word_count,
            "excluded": None if is_counted else ("reference" if s.is_reference else "quote" if s.is_quote else "citation"),
            "match": None,
        }
        if m:
            entry["match"] = {
                "kind": m.kind,
                "score": round(m.score, 1),
                "url": m.source_url,
                "title": titles.get(m.source_url, m.source_url),
                "source_text": m.source_text[:400],
            }
            if is_counted:
                kind_words[m.kind] += s.word_count
                sr = per_source.setdefault(
                    m.source_url,
                    SourceReport(m.source_url, titles.get(m.source_url, m.source_url), origins.get(m.source_url, "web"), 0, 0.0),
                )
                sr.matched_words += s.word_count
                sr.kinds[m.kind] = sr.kinds.get(m.kind, 0) + 1
        ann.append(entry)

    coverage = {k: sum(1 for u in texts if origins.get(u) == k) for k in ("web", "wikipedia", "academic", "corpus")}
    # web_searched: the engines accepted our queries (so "no web matches" is meaningful)
    coverage["web_searched"] = bool(use_web) and not status.get("web_blocked") and not (status.get("timed_out") and coverage["web"] == 0)
    coverage["web_blocked"] = bool(status.get("web_blocked"))
    coverage["queries"] = len(queries)
    coverage["complete"] = coverage["web_searched"] and not status.get("timed_out")

    denom = max(1, counted_words)
    for sr in per_source.values():
        sr.percent = round(100 * sr.matched_words / denom, 1)
    sources = sorted(per_source.values(), key=lambda s: -s.matched_words)
    matched_total = sum(kind_words.values())

    return PlagiarismResult(
        similarity=round(100 * matched_total / denom, 1),
        exact_percent=round(100 * kind_words["exact"] / denom, 1),
        near_percent=round(100 * kind_words["near"] / denom, 1),
        paraphrase_percent=round(100 * kind_words["paraphrase"] / denom, 1),
        total_words=total_words,
        counted_words=counted_words,
        sources=sources,
        sentences=ann,
        queries=queries,
        candidates_checked=len(texts),
        elapsed=round(time.perf_counter() - t0, 2),
        warnings=warnings,
        coverage=coverage,
    )


def to_dict(r: PlagiarismResult) -> dict:
    d = asdict(r)
    return d
