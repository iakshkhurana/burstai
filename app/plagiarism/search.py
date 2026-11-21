"""Candidate source retrieval from the open web, free and key-less.

Strategy (same idea Turnitin/Copyscape use, minus the private index):
  * pick the most *distinctive* sentences of the document,
  * turn each into an exact-phrase query of 8-12 consecutive words,
  * ask DuckDuckGo (via the `ddgs` package; no API key) for matching pages,
  * also query Wikipedia's search API directly since it is the #1 copy source.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .. import cache, config
from ..textproc import Sentence, content_words

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=3)  # gentle on the free search engines


@dataclass
class Candidate:
    url: str
    title: str = ""
    snippet: str = ""
    queries: list[str] = field(default_factory=list)
    origin: str = "web"   # web | wikipedia | corpus


def build_queries(sentences: list[Sentence], max_queries: int) -> list[str]:
    """Pick distinctive sentences and build exact-phrase queries."""
    scored = []
    for s in sentences:
        if s.is_reference or s.is_citation or s.word_count < 7:
            continue
        cw = content_words(s.words)
        if len(cw) < 3:
            continue
        # distinctiveness: long content words and lexical variety
        score = sum(min(len(w), 12) for w in cw) / max(1, s.word_count) + len(set(cw)) / 10
        scored.append((score, s))
    scored.sort(key=lambda t: -t[0])

    queries: list[str] = []
    seen: set[str] = set()
    # spread picks across the document rather than clustering
    for _, s in scored:
        phrase = _phrase(s.text)
        key = phrase.lower()
        if not phrase or key in seen:
            continue
        seen.add(key)
        queries.append(f'"{phrase}"')
        if len(queries) >= max_queries:
            break
    return queries


def _phrase(text: str, lo: int = 8, hi: int = 12) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text)
    if len(words) < lo:
        return " ".join(words)
    # choose the window with the longest average word length (most distinctive)
    best, best_score = words[:hi], -1.0
    for i in range(0, max(1, len(words) - hi + 1)):
        win = words[i : i + hi]
        sc = sum(len(w) for w in win) / len(win)
        if sc > best_score:
            best, best_score = win, sc
    return " ".join(best)


SEARCH_TTL = 7 * 86400


def _ddg_search(query: str, n: int) -> list[Candidate]:
    cached = cache.get("search", query, SEARCH_TTL)
    if cached is not None:
        return [Candidate(url=r["url"], title=r["title"], snippet=r["snippet"], queries=[query]) for r in cached]

    rows: list[dict] = []
    definitive = False  # True when an engine answered (even with nothing) rather than timing out
    # ddgs rotates several engines under "auto"; if that fails, try plain DuckDuckGo once more.
    for attempt, backend in enumerate(("auto", "duckduckgo")):
        try:
            from ddgs import DDGS

            with DDGS(timeout=8) as ddg:
                rows = ddg.text(query, max_results=n, backend=backend) or []
            definitive = True
            break
        except Exception as e:  # noqa: BLE001 - network / rate-limit; degrade gracefully
            msg = str(e)
            if "No results" in msg:
                log.debug("ddgs: no results for %r", query)  # normal for original sentences
                definitive = True
                break
            log.warning("ddgs(%s) failed for %r: %s", backend, query, msg[:100])
            time.sleep(1.0 * (attempt + 1))
    out = []
    for r in rows:
        url = r.get("href") or r.get("url") or ""
        if url.startswith("http"):
            out.append(Candidate(url=url, title=r.get("title", ""), snippet=r.get("body", ""), queries=[query]))
    if out or definitive:
        cache.put("search", query, [{"url": c.url, "title": c.title, "snippet": c.snippet} for c in out])
    return out


async def _wiki_search(client: httpx.AsyncClient, query: str, n: int = 3) -> list[Candidate]:
    # Wikipedia's search is strict about exact phrases; use the most distinctive content words instead.
    cw = sorted(set(content_words(query.strip('"').lower().split())), key=len, reverse=True)[:5]
    if len(cw) < 2:
        return []
    terms = " ".join(cw)
    cached = cache.get("wiki", terms, SEARCH_TTL)
    if cached is not None:
        return [Candidate(url=r["url"], title=r["title"], snippet=r["snippet"], queries=[query], origin="wikipedia") for r in cached]
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": terms, "format": "json", "srlimit": n},
            headers={"User-Agent": "burstai/0.1 (open-source plagiarism checker)"},
            timeout=8,
        )
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
    except Exception as e:  # noqa: BLE001
        log.debug("wikipedia search failed: %s", e)
        return []
    out = []
    for h in hits:
        title = h.get("title", "")
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        snippet = re.sub(r"<[^>]+>", "", h.get("snippet", ""))
        out.append(Candidate(url=url, title=title, snippet=snippet, queries=[query], origin="wikipedia"))
    cache.put("wiki", terms, [{"url": c.url, "title": c.title, "snippet": c.snippet} for c in out])
    return out


async def find_candidates(sentences: list[Sentence], deep: bool = False) -> tuple[list[Candidate], list[str]]:
    max_q = config.SEARCH_MAX_QUERIES * (2 if deep else 1)
    queries = build_queries(sentences, max_q)
    if not queries:
        return [], []

    loop = asyncio.get_running_loop()
    per_q = config.SEARCH_RESULTS_PER_QUERY
    ddg_jobs = [loop.run_in_executor(_pool, _ddg_search, q, per_q) for q in queries]
    async with httpx.AsyncClient(follow_redirects=True) as client:
        wiki_jobs = [_wiki_search(client, q) for q in queries[: max(3, len(queries) // 2)]]
        gathered = asyncio.gather(*ddg_jobs, *wiki_jobs, return_exceptions=True)
        try:
            results = await asyncio.wait_for(gathered, timeout=config.SEARCH_PHASE_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("search phase exceeded %ss; using what came back", config.SEARCH_PHASE_TIMEOUT)
            results = [j.result() for j in ddg_jobs if j.done() and not j.cancelled() and not j.exception()]

    merged: dict[str, Candidate] = {}
    for res in results:
        if isinstance(res, Exception):
            continue
        for c in res:
            key = _canon(c.url)
            if key in merged:
                merged[key].queries.extend(c.queries)
            else:
                merged[key] = c
    # pages hit by several distinct queries are the most promising; rank them first
    ranked = sorted(merged.values(), key=lambda c: -len(set(c.queries)))
    return ranked[: config.SEARCH_MAX_CANDIDATES], queries


def _canon(url: str) -> str:
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.rstrip("/").lower()
