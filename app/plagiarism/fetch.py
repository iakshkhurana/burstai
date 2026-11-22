"""Download candidate pages and reduce them to clean body text.

Every outgoing request (including each redirect hop) is checked by an SSRF guard: only http(s)
URLs that resolve to public addresses are fetched, so a hostile search result can never make the
engine read from localhost or the local network.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .. import cache, config
from ..extract import html_to_text, pdf_bytes_to_text
from ..security import is_public_url
from .search import Candidate

log = logging.getLogger(__name__)
PAGE_TTL = 86400
# An honest, contactable identity (required by Wikimedia's robot policy, good manners everywhere).
_UA = "Mozilla/5.0 (compatible; BurstAI/0.2; +https://burstai.online; https://github.com/iakshkhurana/burstai)"


async def _guard(request: httpx.Request) -> None:
    if not await asyncio.to_thread(is_public_url, str(request.url)):
        raise httpx.RequestError("blocked non-public address", request=request)


async def fetch_all(cands: list[Candidate]) -> dict[str, str]:
    """url -> extracted text (only successful fetches are returned)."""
    sem = asyncio.Semaphore(config.FETCH_CONCURRENCY)
    out: dict[str, str] = {}

    async with httpx.AsyncClient(
        follow_redirects=True, max_redirects=5, timeout=config.FETCH_TIMEOUT,
        headers={"User-Agent": _UA, "Accept-Language": "en"}, event_hooks={"request": [_guard]},
    ) as client:

        async def one(c: Candidate) -> None:
            if c.text:  # academic abstracts arrive with their text
                out[c.url] = c.text
                return
            cached = cache.get("page", c.url, PAGE_TTL)
            if cached is not None:
                if cached:
                    out[c.url] = cached
                return
            async with sem:
                text = await fetch_one(client, c.url)
            if text and len(text) > 200:
                out[c.url] = text
                cache.put("page", c.url, text[:400_000])
            else:
                cache.put("page", c.url, "")  # remember failures briefly too

        await asyncio.gather(*(one(c) for c in cands), return_exceptions=True)
    return out


async def fetch_one(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            ctype = r.headers.get("content-type", "")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > config.FETCH_MAX_BYTES:
                    break
            data = b"".join(chunks)
            encoding = r.encoding or "utf-8"
    except Exception as e:  # noqa: BLE001
        log.debug("fetch failed (%s)", type(e).__name__)
        return None

    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            return await asyncio.to_thread(pdf_bytes_to_text, data)
        except Exception:  # noqa: BLE001
            return None
    if "text" in ctype or "html" in ctype or "xml" in ctype or not ctype:
        return await asyncio.to_thread(html_to_text, data.decode(encoding, "ignore"))
    return None
