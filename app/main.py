"""burstai HTTP API + static front-end.

    uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, auth, config, llm
from .aidetect import engine as ai_engine
from .aidetect import llm_judge
from .aidetect.lm import SCORER
from .extract import extract_text
from .grammar import check_grammar
from . import reports
from .plagiarism import corpus
from .plagiarism import engine as plag_engine
from .plagiarism.matcher import embedder_ready, get_embedder
from .textproc import split_sentences

logging.basicConfig(level=os.getenv("BURSTAI_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("burstai")
for noisy in ("ddgs", "primp", "httpx", "trafilatura", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.bootstrap_admin()
    if os.getenv("BURSTAI_PRELOAD", "1") != "0":
        # warm the models in the background so the first request is fast
        asyncio.get_running_loop().run_in_executor(None, SCORER.load)
        asyncio.get_running_loop().run_in_executor(None, get_embedder)
    yield


app = FastAPI(title="burstai", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def engine_token_guard(request: Request, call_next):
    """Optional shared-secret lock for the engine endpoints when they are exposed to a Worker proxy."""
    if config.ENGINE_TOKEN and request.url.path.startswith(("/api/check", "/api/extract", "/api/corpus", "/api/gemini")):
        if request.headers.get("x-engine-token") != config.ENGINE_TOKEN:
            return Response('{"detail":"engine token required"}', status_code=401, media_type="application/json")
    return await call_next(request)


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
    language: str = "auto"
    gemini_key: str = ""  # user's own key from the browser; never stored


class CheckRequest(BaseModel):
    text: str = Field(..., min_length=1)
    options: CheckOptions = CheckOptions()


@app.get("/api/health")
async def health():
    try:
        import torch

        cuda = torch.cuda.is_available()
    except Exception:  # noqa: BLE001 - torch not installed yet
        cuda = False
    return {
        "version": __version__,
        "lm": SCORER.info(),
        "embedder": embedder_ready(),
        "cuda": cuda,
        "gemini": llm_judge.available(),
        "gemini_model": config.GEMINI_MODEL,
        "corpus_docs": len(corpus.list_docs()),
    }


@app.post("/api/check")
async def check(req: CheckRequest):
    text = req.text.replace("\r\n", "\n")
    if len(text) > config.MAX_INPUT_CHARS:
        raise HTTPException(413, f"Text too long (max {config.MAX_INPUT_CHARS} characters)")
    o = req.options
    sents = split_sentences(text)
    if not sents:
        raise HTTPException(400, "No analysable text found")

    tasks = {}
    if o.plagiarism:
        tasks["plagiarism"] = plag_engine.check_plagiarism(
            text,
            exclude_quotes=o.exclude_quotes,
            exclude_references=o.exclude_references,
            exclude_citations=o.exclude_citations,
            use_web=o.use_web,
            use_corpus=o.use_corpus,
            deep=o.deep_search,
            sentences=sents,
        )
    if o.ai:
        tasks["ai"] = ai_engine.detect_ai(text, use_llm_judge=o.llm_judge, sentences=sents, gemini_key=gemini_key)
    if o.grammar:
        tasks["grammar"] = check_grammar(text, o.language, gemini_key)

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    out: dict = {
        "text": text,
        "stats": {"words": sum(s.word_count for s in sents), "sentences": len(sents), "characters": len(text)},
        "sentences": [
            {"index": s.index, "start": s.start, "end": s.end, "quote": s.is_quote, "reference": s.is_reference, "citation": s.is_citation}
            for s in sents
        ],
    }
    for key, res in zip(tasks, results):
        if isinstance(res, BaseException):
            log.exception("%s failed", key, exc_info=res)
            out[key] = {"error": f"{type(res).__name__}: {res}"}
        elif key == "plagiarism":
            out[key] = plag_engine.to_dict(res)
        elif key == "ai":
            out[key] = ai_engine.to_dict(res)
        else:
            out[key] = res
    return out


class GeminiKey(BaseModel):
    key: str


@app.post("/api/gemini/validate")
async def gemini_validate(body: GeminiKey):
    """Round-trip the user's key once so the UI can confirm it works. The key is not stored."""
    return await llm.validate(body.key)


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "File too large (25 MB max)")
    try:
        text = extract_text(file.filename or "", data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read file: {e}") from e
    return {"filename": file.filename, "text": text, "characters": len(text)}


@app.get("/api/corpus")
async def corpus_list():
    return [d.__dict__ for d in corpus.list_docs()]


@app.post("/api/corpus")
async def corpus_add(file: UploadFile = File(...)):
    data = await file.read()
    text = extract_text(file.filename or "", data)
    if len(text.split()) < 20:
        raise HTTPException(400, "Document too short to index")
    return corpus.add(file.filename or "untitled", text).__dict__


@app.delete("/api/corpus/{doc_id}")
async def corpus_delete(doc_id: int):
    if not corpus.delete(doc_id):
        raise HTTPException(404, "Not found")
    return {"deleted": doc_id}


# ------------------------------------------------------------------ accounts
class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


@app.post("/api/auth/signup")
async def auth_signup(body: Credentials, request: Request, response: Response):
    user = auth.signup(body.name, body.email, body.password)
    auth.set_cookie(response, request, auth.create_session(user.id))
    return {"user": user.public()}


@app.post("/api/auth/login")
async def auth_login(body: Credentials, request: Request, response: Response):
    user = auth.login(body.email, body.password)
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


@app.get("/api/scans")
async def scans_list(request: Request):
    return auth.list_scans(auth.require_user(request).id)


@app.post("/api/scans")
async def scans_save(report: dict, request: Request):
    return auth.save_scan(auth.require_user(request).id, report)


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


@app.post("/api/reports")
async def report_save(report: dict):
    try:
        rid = reports.save(report)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"id": rid}


@app.get("/api/reports/{rid}")
async def report_get(rid: str):
    r = reports.load(rid)
    if r is None:
        raise HTTPException(404, "Report not found")
    return r


def _base(request: Request) -> str:
    return config.PUBLIC_URL or str(request.base_url).rstrip("/")


def _page(name: str, request: Request, *, noindex: bool = False) -> HTMLResponse:
    if config.ENGINE_TOKEN:
        # Engine-only mode (behind a Worker proxy): never serve the UI from the engine host, so the
        # public tunnel URL cannot be used to run checks without the shared token.
        site = config.PUBLIC_URL or "/"
        return HTMLResponse(
            f'<!doctype html><meta charset="utf-8"><title>BurstAI engine</title><body style="font:16px system-ui;padding:40px">'
            f"<p>BurstAI engine is running. Use the site at <a href=\"{site}\">{site}</a>.</p>",
            status_code=404, headers={"X-Robots-Tag": "noindex, nofollow"},
        )
    html = (STATIC / name).read_text(encoding="utf-8").replace("__BASE__", _base(request))
    headers = {"Cache-Control": "no-cache"}
    if noindex:
        headers["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(html, headers=headers)


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def index(request: Request):
    return _page("index.html", request)


@app.api_route("/check", methods=["GET", "HEAD"], include_in_schema=False)
async def check_page(request: Request):
    return _page("check.html", request, noindex=True)


@app.api_route("/report", methods=["GET", "HEAD"], include_in_schema=False)
async def report_page(request: Request):
    return _page("report.html", request, noindex=True)


@app.api_route("/v/{rid}", methods=["GET", "HEAD"], include_in_schema=False)
async def verify_page(rid: str, request: Request):
    return _page("report.html", request, noindex=True)


@app.api_route("/login", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/signup", methods=["GET", "HEAD"], include_in_schema=False)
async def auth_page(request: Request):
    return _page("auth.html", request, noindex=True)


@app.api_route("/me", methods=["GET", "HEAD"], include_in_schema=False)
async def me_page(request: Request):
    return _page("me.html", request, noindex=True)


@app.get("/robots.txt", include_in_schema=False)
async def robots(request: Request):
    body = f"User-agent: *\nAllow: /\nDisallow: /check\nDisallow: /report\nDisallow: /v/\nDisallow: /api/\n\nSitemap: {_base(request)}/sitemap.xml\n"
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    base = _base(request)
    mtime = datetime.fromtimestamp((STATIC / "index.html").stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{base}/</loc><lastmod>{mtime}</lastmod><changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n"
    )
    return Response(body, media_type="application/xml")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
