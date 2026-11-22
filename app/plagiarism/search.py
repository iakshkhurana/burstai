"""Candidate source retrieval from the open web and open academic indexes - free and key-less.

Strategy (same idea Turnitin/Copyscape use, minus the private index):
  * pick the most *distinctive* sentences of the document,
  * turn each into a query of 8-12 consecutive words,
  * ask web search engines (via the `ddgs` package; no API key) for matching pages,
  * query Wikipedia's search API directly since it is the #1 copy source,
  * query Europe PMC and Crossref for papers whose abstracts may have been copied.

Privacy: queries are fragments of the user's document. They are sent to the search providers (that
is unavoidable for a web check and is disclosed on /privacy) but are never written to logs, and the
local cache is keyed by a hash of the query rather than its text.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from .. import cache, config
from ..textproc import Sentence, content_words, tokenize_words, word_tokens

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=3)  # gentle on the free search engines
# Wikimedia and Crossref ask automated clients to identify themselves with a contact URL.
_UA = {"User-Agent": "BurstAI/0.2 (https://burstai.online; https://github.com/iakshkhurana/burstai) python-httpx"}
SEARCH_TTL = 7 * 86400
EMPTY_TTL = 86400          # "nothing found" is remembered for a day only
HEALTH_TTL = 600           # re-test blocked search engines every 10 minutes
_health = {"ok": None, "at": 0.0}
# Real web engines only: Wikipedia has its own, better-behaved API below.
WEB_BACKENDS = "duckduckgo,brave,yahoo,mojeek,startpage"
# sponsored / tracking redirects that search front-ends mix into results
_AD_URL = re.compile(r"bing\.com/aclick|duckduckgo\.com/y\.js|googleadservices\.|doubleclick\.net|/aclk\?|[?&]ad_domain=", re.I)


@dataclass
class Candidate:
    url: str
    title: str = ""
    snippet: str = ""
    queries: list[str] = field(default_factory=list)
    origin: str = "web"        # web | wikipedia | academic | corpus
    text: str | None = None    # already-known full text (academic abstracts); skips the page fetch


def _k(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:40]


def build_queries(sentences: list[Sentence], max_queries: int) -> list[str]:
    """Pick distinctive sentences and turn each into a search phrase of 8-12 consecutive words."""
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
    for _, s in scored:
        phrase = _phrase(s.text)
        key = phrase.lower()
        if not phrase or key in seen:
            continue
        seen.add(key)
        queries.append(phrase)
        if len(queries) >= max_queries:
            break
    return queries


def _phrase(text: str, lo: int = 8, hi: int = 12) -> str:
    words = word_tokens(text)
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


def _terms(query: str, n: int = 6) -> str:
    """The most distinctive content words of a phrase query (for keyword-style APIs)."""
    cw = sorted(set(content_words(tokenize_words(query))), key=len, reverse=True)[:n]
    return " ".join(cw) if len(cw) >= 2 else ""


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


# ----------------------------------------------------------------------------- web search
def web_search_available() -> bool:
    """Canary: free search front-ends answer a blocked client with "no results", which is
    indistinguishable from an original sentence. So we ask for something that certainly exists;
    if even that comes back empty, the engines are refusing us and web results cannot be trusted."""
    now = time.monotonic()
    if _health["ok"] is not None and now - _health["at"] < HEALTH_TTL:
        return _health["ok"]
    ok = False
    try:
        from ddgs import DDGS

        with DDGS(timeout=8) as ddg:
            # same style as the real queries (an unquoted run of words), real web engines only
            ok = bool(ddg.text("to be or not to be that is the question whether tis nobler", max_results=2, backend=WEB_BACKENDS))
    except Exception as e:  # noqa: BLE001
        log.warning("web search canary failed (%s): search engines are not answering this server", type(e).__name__)
    _health.update(ok=ok, at=now)
    return ok


def _ddg_search(query: str, n: int, fallback: bool = False) -> list[Candidate]:
    """Search real web engines for a run of words from the document.

    Queries are deliberately NOT quoted: the free front-ends currently return nothing for long
    exact-phrase queries, while the same words unquoted bring back the pages that contain them.
    Relevance is then decided by the matcher on the downloaded text, not by the search engine.
    With fallback=True an empty answer is retried once with just the distinctive keywords."""
    cached = cache.get("search", _k(query), SEARCH_TTL)
    if cached is None and cache.get("search0", _k(query), EMPTY_TTL) is not None:
        cached = []
    if cached is not None:
        return [Candidate(url=r["url"], title=r["title"], snippet=r["snippet"], queries=[query]) for r in cached]

    rows: list[dict] = []
    definitive = False  # True when an engine answered (even with nothing) rather than timing out
    attempts = [query] + ([_terms(query, 9)] if fallback and _terms(query, 9) else [])
    for attempt, q in enumerate(attempts):
        try:
            from ddgs import DDGS

            with DDGS(timeout=8) as ddg:
                rows = ddg.text(q, max_results=n, backend=WEB_BACKENDS) or []
            definitive = True
            if rows:
                break
        except Exception as e:  # noqa: BLE001 - network / rate-limit; degrade gracefully
            if "No results" in str(e):
                definitive = True  # normal for original sentences; maybe the keyword form finds something
                continue
            log.warning("web search failed (%s)", type(e).__name__)  # never log the query text
            time.sleep(1.0 * (attempt + 1))
    out = []
    for r in rows:
        url = r.get("href") or r.get("url") or ""
        if url.startswith("http") and not _AD_URL.search(url):
            out.append(Candidate(url=url, title=r.get("title", ""), snippet=r.get("body", ""), queries=[query]))
    if out:
        cache.put("search", _k(query), [{"url": c.url, "title": c.title, "snippet": c.snippet} for c in out])
    elif definitive and web_search_available():
        cache.put("search0", _k(query), [])  # a genuine "nothing found"; never cache a block as a miss
    return out


async def _wiki_search(client: httpx.AsyncClient, query: str, n: int = 3) -> list[Candidate]:
    terms = _terms(query, 5)
    if not terms:
        return []
    cached = cache.get("wiki", _k(terms), SEARCH_TTL)
    if cached is not None:
        return [Candidate(url=r["url"], title=r["title"], snippet=r["snippet"], queries=[query], origin="wikipedia") for r in cached]
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": terms, "format": "json", "srlimit": n},
            headers=_UA, timeout=8,
        )
        r.raise_for_status()
        hits = r.json().get("query", {}).get("search", [])
    except Exception as e:  # noqa: BLE001
        log.debug("wikipedia search failed (%s)", type(e).__name__)
        return []
    out = []
    for h in hits:
        title = h.get("title", "")
        url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        out.append(Candidate(url=url, title=title, snippet=_strip_tags(h.get("snippet", "")), queries=[query], origin="wikipedia"))
    cache.put("wiki", _k(terms), [{"url": c.url, "title": c.title, "snippet": c.snippet} for c in out])
    return out


# ----------------------------------------------------------------------------- open academic indexes
def _acad_from_cache(rows: list[dict], query: str) -> list[Candidate]:
    return [Candidate(url=r["url"], title=r["title"], queries=[query], origin="academic", text=r["text"]) for r in rows]


async def _europepmc(client: httpx.AsyncClient, query: str, n: int = 3) -> list[Candidate]:
    """Europe PMC: free, key-less, returns abstracts (life sciences, plus many preprints)."""
    terms = _terms(query)
    if not terms:
        return []
    cached = cache.get("epmc", _k(terms), SEARCH_TTL)
    if cached is not None:
        return _acad_from_cache(cached, query)
    try:
        r = await client.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": terms, "format": "json", "resultType": "core", "pageSize": n},
            headers=_UA, timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("resultList", {}).get("result", [])
    except Exception as e:  # noqa: BLE001
        log.debug("europepmc failed (%s)", type(e).__name__)
        return []
    rows = []
    for it in items:
        abstract = _strip_tags(it.get("abstractText", ""))
        title = _strip_tags(it.get("title", ""))
        if len(abstract) < 200:
            continue
        doi = it.get("doi")
        url = f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{it.get('source', 'MED')}/{it.get('id', '')}"
        rows.append({"url": url, "title": title or url, "text": f"{title}. {abstract}"})
    cache.put("epmc", _k(terms), rows)
    return _acad_from_cache(rows, query)


async def _crossref(client: httpx.AsyncClient, query: str, n: int = 3) -> list[Candidate]:
    """Crossref: free, key-less; only works that deposit an abstract are useful to us."""
    terms = _terms(query)
    if not terms:
        return []
    cached = cache.get("crossref", _k(terms), SEARCH_TTL)
    if cached is not None:
        return _acad_from_cache(cached, query)
    try:
        r = await client.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": terms, "rows": n, "filter": "has-abstract:true", "select": "title,DOI,abstract"},
            headers=_UA, timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
    except Exception as e:  # noqa: BLE001
        log.debug("crossref failed (%s)", type(e).__name__)
        return []
    rows = []
    for it in items:
        abstract = _strip_tags(it.get("abstract", ""))
        title = _strip_tags(" ".join(it.get("title") or []))
        if len(abstract) < 200 or not it.get("DOI"):
            continue
        rows.append({"url": f"https://doi.org/{it['DOI']}", "title": title or it["DOI"], "text": f"{title}. {abstract}"})
    cache.put("crossref", _k(terms), rows)
    return _acad_from_cache(rows, query)


# ----------------------------------------------------------------------------- orchestration
async def find_candidates(sentences: list[Sentence], deep: bool = False) -> tuple[list[Candidate], list[str], dict]:
    """Returns (candidates, queries, provider status)."""
    max_q = config.SEARCH_MAX_QUERIES * (2 if deep else 1)
    queries = build_queries(sentences, max_q)
    status = {"web": 0, "wikipedia": 0, "academic": 0, "timed_out": False}
    if not queries:
        return [], [], status

    loop = asyncio.get_running_loop()
    per_q = config.SEARCH_RESULTS_PER_QUERY
    web_ok = await loop.run_in_executor(_pool, web_search_available)
    status["web_blocked"] = not web_ok
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # when the engines refuse us, only replay cached answers instead of hammering them
        tasks: list[asyncio.Future] = [loop.run_in_executor(_pool, _ddg_search, q, per_q, i < 4) for i, q in enumerate(queries)] if web_ok else []
        side = queries[: max(3, len(queries) // 2)]
        tasks += [asyncio.ensure_future(_wiki_search(client, q)) for q in side]
        tasks += [asyncio.ensure_future(_europepmc(client, q)) for q in side[:4]]
        tasks += [asyncio.ensure_future(_crossref(client, q)) for q in side[:4]]
        if not web_ok:  # still serve anything cached from earlier, healthy runs
            for q in queries:
                hit = cache.get("search", _k(q), SEARCH_TTL)
                if hit:
                    f = loop.create_future()
                    f.set_result([Candidate(url=r["url"], title=r["title"], snippet=r["snippet"], queries=[q]) for r in hit])
                    tasks.append(f)
        done, pending = await asyncio.wait(tasks, timeout=config.SEARCH_PHASE_TIMEOUT)
        for t in pending:
            t.cancel()
        status["timed_out"] = bool(pending)

    merged: dict[str, Candidate] = {}
    for t in done:
        if t.cancelled() or t.exception():
            continue
        for c in t.result():
            status["web" if c.origin == "web" else c.origin] = status.get("web" if c.origin == "web" else c.origin, 0) + 1
            key = _canon(c.url)
            if key in merged:
                merged[key].queries.extend(c.queries)
            else:
                merged[key] = c
    # pages hit by several distinct queries are the most promising; rank them first
    ranked = sorted(merged.values(), key=lambda c: -len(set(c.queries)))
    return ranked[: config.SEARCH_MAX_CANDIDATES], queries, status


def _canon(url: str) -> str:
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.rstrip("/").lower()
