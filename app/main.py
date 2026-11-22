"""burstai HTTP API + static front-end.

    uvicorn app.main:app

Two ways to run:
  * stand-alone  - this process serves the site, accounts and the checking engine;
  * engine-only  - BURSTAI_ENGINE_TOKEN is set: only the engine endpoints answer, and only to
                   requests carrying the shared token (i.e. from the Cloudflare Worker).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, auth, config, llm, reports
from .aidetect import engine as ai_engine
from .aidetect import llm_judge
from .aidetect.lm import SCORER
from .extract import extract_text
from .grammar import check_grammar
from .plagiarism import corpus
from .plagiarism import engine as plag_engine
from .plagiarism.matcher import embedder_ready, get_embedder
from .security import CSP, SECURITY_HEADERS, attest, limit, text_hash, trusted_proxy
from .textproc import split_sentences

logging.basicConfig(level=os.getenv("BURSTAI_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("burstai")
for noisy in ("ddgs", "primp", "httpx", "httpx2", "trafilatura", "urllib3", "google_genai", "google_genai.models"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

STATIC = Path(__file__).resolve().parent.parent / "static"
ENGINE_PATHS = ("/api/check", "/api/extract", "/api/corpus", "/api/gemini")


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.bootstrap_admin()
    if os.getenv("BURSTAI_PRELOAD", "1") != "0":
        # warm the models in the background so the first request is fast
        asyncio.get_running_loop().run_in_executor(None, SCORER.load)
        asyncio.get_running_loop().run_in_executor(None, get_embedder)
    yield


# No CORS middleware on purpose: the browser only ever talks to its own origin (the Worker proxies
# to the engine server-side), so cross-origin access stays blocked by default.
app = FastAPI(title="burstai", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard_and_headers(request: Request, call_next):
    path = request.url.path
    # Optional shared-secret lock for the engine endpoints when they are exposed to a Worker proxy.
    if config.ENGINE_TOKEN and path.startswith(ENGINE_PATHS) and not trusted_proxy(request):
        return JSONResponse({"detail": "engine token required"}, status_code=401)
    cl = request.headers.get("content-length", "")
    if cl.isdigit() and int(cl) > config.MAX_UPLOAD_BYTES + 1_000_000:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    # CSRF defence in depth: browsers send Origin on cross-site state-changing requests.
    if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith("/api/") and not trusted_proxy(request):
        origin = request.headers.get("origin", "")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/") and path.endswith(".html"):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "Invalid request."}, status_code=400)


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Something went wrong on our side. Please try again."}, status_code=500)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not request.url.path.startswith("/api/") and not config.ENGINE_TOKEN:
        page = STATIC / "404.html"
        if page.exists():
            return HTMLResponse(page.read_text(encoding="utf-8").replace("__BASE__", _base(request)), status_code=404,
                                headers={"X-Robots-Tag": "noindex, nofollow"})
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


# ----------------------------------------------------------------------------- checking engine
class CheckOptions(BaseModel):
    plagiarism: bool = True
    ai: bool = True
    grammar: bool = True
    exclude_quotes: bool = True
    exclude_references: bool = True
    exclude_citations: bool = True
    use_web: bool = True
    use_corpus: bool = True
    deep_search: bool = False
    llm_judge: bool = True
    language: str = Field("auto", max_length=12)
    gemini_key: str = Field("", max_length=200)  # user's own key from the browser; never stored or logged


class CheckRequest(BaseModel):
    text: str = Field(..., min_length=1)
    options: CheckOptions = CheckOptions()


def _owner(request: Request) -> str | None:
    """Whose private document bank this request may use."""
    if config.ENGINE_TOKEN:  # engine-only: the trusted Worker tells us who is logged in
        uid = request.headers.get("x-burstai-user", "").strip()
        return f"u{uid}" if uid.isdigit() else None
    user = auth.current_user(request)
    return f"u{user.id}" if user else "local"


@app.get("/api/health")
async def health():
    lm = SCORER.info()
    return {
        "version": __version__,
        "lm": {"loaded": lm["loaded"], "device": lm["device"], "observer": lm["observer"], "performer": lm["performer"], "error": bool(lm["error"])},
        "embedder": embedder_ready(),
        "gemini": llm_judge.available(),
        "gemini_model": config.GEMINI_MODEL,
        "max_chars": config.MAX_INPUT_CHARS,
    }


async def _bounded(name: str, coro):
    """Run one part of the scan with a time budget so a slow provider cannot sink the whole request."""
    try:
        return await asyncio.wait_for(coro, timeout=config.TASK_TIMEOUT)
    except asyncio.TimeoutError:
        return TimeoutError(name)


@app.post("/api/check")
async def check(req: CheckRequest, request: Request):
    limit(request, "scan-m", config.RATE_SCANS_PER_MIN, 60)
    limit(request, "scan-h", config.RATE_SCANS_PER_HOUR, 3600)
    text = req.text.replace("\r\n", "\n").replace("\x00", "")
    if len(text) > config.MAX_INPUT_CHARS:
        raise HTTPException(413, f"This text is too long for one scan (limit {config.MAX_INPUT_CHARS:,} characters, about {config.MAX_INPUT_CHARS // 6:,} words). Split it and scan the parts.")
    o = req.options
    # the user's own Gemini key, from the browser (never stored or logged); header wins over body
    gemini_key = (request.headers.get("x-gemini-key", "") or o.gemini_key or "").strip() or None
    sents = split_sentences(text)
    n_words = sum(s.word_count for s in sents)
    if not sents or n_words < 5:
        raise HTTPException(400, "There is not enough text to analyse. Paste at least a few sentences.")

    tasks = {}
    if o.plagiarism:
        tasks["plagiarism"] = _bounded("plagiarism", plag_engine.check_plagiarism(
            text,
            exclude_quotes=o.exclude_quotes,
            exclude_references=o.exclude_references,
            exclude_citations=o.exclude_citations,
            use_web=o.use_web,
            use_corpus=o.use_corpus,
            deep=o.deep_search,
            sentences=sents,
            corpus_owner=_owner(request),
        ))
    if o.ai:
        tasks["ai"] = _bounded("ai", ai_engine.detect_ai(text, use_llm_judge=o.llm_judge, sentences=sents, gemini_key=gemini_key))
    if o.grammar:
        tasks["grammar"] = _bounded("grammar", check_grammar(text, o.language, gemini_key))

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out: dict = {
        "text": text,
        "stats": {"words": n_words, "sentences": len(sents), "characters": len(text)},
        "sentences": [
            {"index": s.index, "start": s.start, "end": s.end, "quote": s.is_quote, "reference": s.is_reference, "citation": s.is_citation}
            for s in sents
        ],
    }
    for key, res in zip(tasks, results):
        if isinstance(res, TimeoutError):
            out[key] = {"error": "This part of the scan took too long and was stopped. Try again, or scan a shorter text."}
        elif isinstance(res, BaseException):
            log.error("%s failed: %s", key, type(res).__name__, exc_info=res)
            out[key] = {"error": "This part of the scan failed. Please try again."}
        elif key == "plagiarism":
            out[key] = plag_engine.to_dict(res)
        elif key == "ai":
            out[key] = ai_engine.to_dict(res)
        else:
            out[key] = res
    out["attestation"] = _attest(text, n_words, len(sents), o, out)
    return out


def _attest(text: str, n_words: int, n_sents: int, o: CheckOptions, out: dict) -> dict:
    """What this engine vouches for: scores, sources and a hash of the exact text it checked.
    The browser cannot alter it without breaking the signature (see app/security.py)."""
    P, A, G = out.get("plagiarism") or {}, out.get("ai") or {}, out.get("grammar") or {}
    plag_ok, ai_ok = "similarity" in P, A.get("ai_probability") is not None
    return attest({
        "v": 1,
        "text_sha256": text_hash(text),
        "words": n_words,
        "sentences": n_sents,
        "similarity": P["similarity"] if plag_ok else None,
        "exact": P.get("exact_percent") if plag_ok else None,
        "near": P.get("near_percent") if plag_ok else None,
        "paraphrase": P.get("paraphrase_percent") if plag_ok else None,
        "pages_compared": P.get("candidates_checked") if plag_ok else None,
        "coverage": P.get("coverage") if plag_ok else None,
        "sources": [{"url": s["url"][:500], "title": (s["title"] or "")[:160], "origin": s["origin"], "percent": s["percent"]} for s in P.get("sources", [])[:15]],
        "ai": A.get("ai_probability") if ai_ok else None,
        "ai_verdict": A.get("verdict") if A.get("verdict") else None,
        "ai_confidence": A.get("confidence") if ai_ok else None,
        "grammar_issues": len(G["issues"]) if G.get("available") else None,
        "exclusions": {"quotes": o.exclude_quotes, "references": o.exclude_references, "citations": o.exclude_citations},
        "checked_at": int(time.time()),
        "engine": __version__,
    })


class GeminiKey(BaseModel):
    key: str = Field(..., max_length=200)


@app.post("/api/gemini/validate")
async def gemini_validate(body: GeminiKey, request: Request):
    """Round-trip the user's key once so the UI can confirm it works. The key is not stored."""
    limit(request, "gemini", 12, 3600)
    return await llm.validate(body.key)


async def _read_upload(file: UploadFile) -> tuple[str, str]:
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large ({config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB max).")
    name = os.path.basename(file.filename or "document")[:120]
    try:
        text = await asyncio.to_thread(extract_text, name, data)
    except Exception as e:  # noqa: BLE001
        log.info("extract failed (%s)", type(e).__name__)
        raise HTTPException(400, "Could not read this file. Supported formats: .docx, .pdf, .txt, .md, .tex, .html. Scanned PDFs (images) contain no selectable text.") from e
    if not text.strip():
        raise HTTPException(400, "No text found in this file. If it is a scanned PDF, it contains images rather than text.")
    return name, text.replace("\x00", "")


@app.post("/api/extract")
async def extract(request: Request, file: UploadFile = File(...)):
    limit(request, "extract", 60, 3600)
    name, text = await _read_upload(file)
    truncated = len(text) > config.MAX_INPUT_CHARS
    return {"filename": name, "text": text[: config.MAX_INPUT_CHARS], "characters": min(len(text), config.MAX_INPUT_CHARS), "truncated": truncated}


def _need_owner(request: Request) -> str:
    owner = _owner(request)
    if not owner:
        raise HTTPException(401, "Log in to use your own document bank.")
    return owner


@app.get("/api/corpus")
async def corpus_list(request: Request):
    return [d.__dict__ for d in corpus.list_docs(_need_owner(request))]


@app.post("/api/corpus")
async def corpus_add(request: Request, file: UploadFile = File(...)):
    owner = _need_owner(request)
    limit(request, "corpus", 60, 3600)
    name, text = await _read_upload(file)
    if len(text.split()) < 20:
        raise HTTPException(400, "Document too short to index")
    try:
        return corpus.add(owner, name, text).__dict__
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/corpus")
async def corpus_clear(request: Request):
    """Erase the caller's whole document bank (used when an account is deleted)."""
    return {"deleted": corpus.delete_owner(_need_owner(request))}


@app.delete("/api/corpus/{doc_id}")
async def corpus_delete(doc_id: int, request: Request):
    if not corpus.delete(_need_owner(request), doc_id):
        raise HTTPException(404, "Not found")
    return {"deleted": doc_id}


# ----------------------------------------------------------------------------- accounts
class Credentials(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=400)
    name: str = Field("", max_length=120)


class Password(BaseModel):
    password: str = Field(..., max_length=400)


@app.post("/api/auth/signup")
async def auth_signup(body: Credentials, request: Request, response: Response):
    limit(request, "signup", 6, 3600)
    user = await asyncio.to_thread(auth.signup, body.name, body.email, body.password)
    auth.set_cookie(response, request, auth.create_session(user.id))
    return {"user": user.public()}


@app.post("/api/auth/login")
async def auth_login(body: Credentials, request: Request, response: Response):
    limit(request, "login", 10, 600)
    user = await asyncio.to_thread(auth.login, body.email, body.password)
    auth.set_cookie(response, request, auth.create_session(user.id))
    return {"user": user.public()}


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response):
    auth.destroy_session(request.cookies.get(auth.COOKIE))
    auth.clear_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    u = auth.current_user(request)
    return {"user": u.public() if u else None}


@app.post("/api/auth/delete")
async def auth_delete(body: Password, request: Request, response: Response):
    """Erase the account and all data stored for it. Requires the password again."""
    user = auth.require_user(request)
    limit(request, "login", 10, 600)
    await asyncio.to_thread(auth.login, user.email, body.password)  # raises 401 on a wrong password
    auth.delete_account(user.id)
    auth.clear_cookie(response)
    return {"deleted": True}


@app.get("/api/scans")
async def scans_list(request: Request):
    return auth.list_scans(auth.require_user(request).id)


@app.post("/api/scans")
async def scans_save(report: dict, request: Request):
    user = auth.require_user(request)
    limit(request, "scans", 120, 3600)
    return auth.save_scan(user.id, report)


@app.get("/api/scans/{scan_id}")
async def scans_get(scan_id: int, request: Request):
    r = auth.get_scan(auth.require_user(request).id, scan_id)
    if r is None:
        raise HTTPException(404, "Not found")
    return r


@app.delete("/api/scans/{scan_id}")
async def scans_delete(scan_id: int, request: Request):
    if not auth.delete_scan(auth.require_user(request).id, scan_id):
        raise HTTPException(404, "Not found")
    return {"deleted": scan_id}


# ----------------------------------------------------------------------------- verification copies
@app.post("/api/reports")
async def report_save(body: dict, request: Request):
    limit(request, "reports", 30, 3600)
    try:
        rid = reports.save(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": rid}


@app.get("/api/reports/{rid}")
async def report_get(rid: str):
    r = reports.load(rid)
    if r is None:
        raise HTTPException(404, "Report not found")
    return r


# ----------------------------------------------------------------------------- pages
def _base(request: Request) -> str:
    return config.PUBLIC_URL or str(request.base_url).rstrip("/")


def _page(name: str, request: Request, *, noindex: bool = False) -> HTMLResponse:
    if config.ENGINE_TOKEN:
        # Engine-only mode (behind a Worker proxy): never serve the UI from the engine host, so the
        # public tunnel URL cannot be used to run checks without the shared token.
        return HTMLResponse(
            '<!doctype html><meta charset="utf-8"><title>BurstAI engine</title><body style="font:16px system-ui;padding:40px">'
            "<p>BurstAI engine is running. Please use the BurstAI site.</p>",
            status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"},
        )
    html = (STATIC / name).read_text(encoding="utf-8").replace("__BASE__", _base(request))
    headers = {"Cache-Control": "no-cache"}
    if noindex:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(html, headers=headers)


PAGES = {"/": ("index.html", False), "/check": ("check.html", True), "/report": ("report.html", True), "/login": ("auth.html", True),
         "/signup": ("auth.html", True), "/me": ("me.html", True), "/privacy": ("privacy.html", False), "/terms": ("terms.html", False)}


def _make_page(file: str, noindex: bool):
    async def handler(request: Request):
        return _page(file, request, noindex=noindex)
    return handler


for _path, (_file, _noindex) in PAGES.items():
    app.add_api_route(_path, _make_page(_file, _noindex), methods=["GET", "HEAD"], include_in_schema=False)


@app.api_route("/v/{rid}", methods=["GET", "HEAD"], include_in_schema=False)
async def verify_page(rid: str, request: Request):
    return _page("report.html", request, noindex=True)


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request):
    body = (
        "User-agent: *\nAllow: /\nDisallow: /check\nDisallow: /report\nDisallow: /me\nDisallow: /login\nDisallow: /signup\n"
        f"Disallow: /v/\nDisallow: /api/\n\nSitemap: {_base(request)}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    base = _base(request)
    mtime = datetime.fromtimestamp((STATIC / "index.html").stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    urls = "".join(
        f"  <url><loc>{base}{p}</loc><lastmod>{mtime}</lastmod><changefreq>{f}</changefreq><priority>{pr}</priority></url>\n"
        for p, f, pr in (("/", "weekly", "1.0"), ("/privacy", "yearly", "0.3"), ("/terms", "yearly", "0.3"))
    )
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n', media_type="application/xml")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
