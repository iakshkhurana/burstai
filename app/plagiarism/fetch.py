"""Download candidate pages and reduce them to clean body text."""
from __future__ import annotations

import asyncio
import logging

import httpx

from .. import cache, config
from ..extract import html_to_text, pdf_bytes_to_text
from .search import Candidate

log = logging.getLogger(__name__)
PAGE_TTL = 86400
_UA = "Mozilla/5.0 (compatible; burstai/0.1; +https://github.com/burstai) open-source plagiarism checker"


async def fetch_all(cands: list[Candidate]) -> dict[str, str]:
    """url -> extracted text (only successful fetches are returned)."""
    sem = asyncio.Semaphore(config.FETCH_CONCURRENCY)
    out: dict[str, str] = {}

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=config.FETCH_TIMEOUT, headers={"User-Agent": _UA, "Accept-Language": "en"}
    ) as client:

        async def one(c: Candidate) -> None:
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
    except Exception as e:  # noqa: BLE001
        log.debug("fetch failed %s: %s", url, e)
        return None

    if "pdf" in ctype or url.lower().endswith(".pdf"):
        try:
            return pdf_bytes_to_text(data)
        except Exception:  # noqa: BLE001
            return None
    if "text" in ctype or "html" in ctype or "xml" in ctype or not ctype:
        return html_to_text(data.decode(r.encoding or "utf-8", "ignore"))
    return None
