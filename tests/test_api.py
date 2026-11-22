"""API tests with FastAPI's TestClient. No model downloads and no reliance on the network for the
assertions made here (see tests/conftest.py for the isolated environment)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app

client = TestClient(app)
ENGLISH = (
    "This is a simple paragraph that should be analysed by the checker. It has several sentences so that the "
    "tokeniser and the report builder have something to work with. Nothing in it is copied from anywhere, and "
    "it was written only for this test of the application."
)
OPTS = {"plagiarism": False, "grammar": False, "ai": True, "llm_judge": False, "gemini_key": ""}


@pytest.fixture(autouse=True)
def _fresh_limits():
    security.LIMITER._hits.clear()
    client.cookies.clear()


def _scan(text=ENGLISH, options=OPTS):
    r = client.post("/api/check", json={"text": text, "options": options})
    assert r.status_code == 200, r.text
    return r.json()


# ----------------------------------------------------------------------------- pages + headers
def test_pages_and_security_headers():
    for p in ("/", "/check", "/report", "/login", "/signup", "/me", "/privacy", "/terms", "/v/BA-00000-00000", "/robots.txt", "/sitemap.xml"):
        assert client.get(p).status_code == 200, p
    r = client.get("/")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "__BASE__" not in r.text
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert "noindex" in client.get("/check").headers["x-robots-tag"]


def test_no_third_party_requests_in_pages():
    for p in ("/", "/check", "/report", "/login", "/me", "/privacy", "/terms"):
        html = client.get(p).text
        assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html, p
        assert "<script>" not in html, f"inline script on {p} would violate the CSP"


def test_unknown_page_is_a_friendly_404_and_api_404_is_json():
    r = client.get("/definitely-not-a-page")
    assert r.status_code == 404 and "Page not found" in r.text
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_cross_site_post_is_blocked():
    r = client.post("/api/auth/logout", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


# ----------------------------------------------------------------------------- scanning
def test_check_returns_signed_attestation():
    d = _scan()
    att = d["attestation"]
    assert security.verify_attestation(att["payload"], att["sig"])
    payload = json.loads(att["payload"])
    assert payload["text_sha256"] == security.text_hash(ENGLISH)
    assert payload["words"] == d["stats"]["words"]
    # tampering with any character breaks the signature
    assert not security.verify_attestation(att["payload"].replace('"words":', '"words":1'), att["sig"])


def test_validation_errors_are_friendly():
    assert client.post("/api/check", json={"text": "hi"}).status_code == 400
    assert client.post("/api/check", json={"nope": 1}).json()["detail"] == "Invalid request."
    too_long = client.post("/api/check", json={"text": "word " * 60000, "options": OPTS})
    assert too_long.status_code == 413 and "too long" in too_long.json()["detail"]


def test_ai_detection_refuses_non_english_instead_of_guessing():
    hindi = "यह एक परीक्षण वाक्य है जो हिंदी में लिखा गया है। " * 12
    d = _scan(hindi)
    assert d["stats"]["words"] > 50                      # Unicode tokenisation works
    assert d["ai"]["ai_probability"] is None and d["ai"]["supported"] is False
    hinglish = "yaar kal ka test bilkul bakwaas gaya maine teen din se sirf unit do padha tha aur paper mein kuch nahi aaya " * 4
    assert _scan(hinglish)["ai"]["ai_probability"] is None


def test_rate_limit_returns_429_with_retry_after():
    for _ in range(6):
        _scan()
    r = client.post("/api/check", json={"text": ENGLISH, "options": OPTS})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "Too many" in r.json()["detail"]


def test_extract_rejects_garbage_cleanly():
    r = client.post("/api/extract", files={"file": ("x.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 400 and "Could not read" in r.json()["detail"]
    r = client.post("/api/extract", files={"file": ("ok.txt", ENGLISH.encode(), "text/plain")})
    assert r.status_code == 200 and r.json()["text"].startswith("This is a simple")


# ----------------------------------------------------------------------------- signed reports
def test_signed_report_roundtrip_and_forgery_is_rejected():
    att = _scan()["attestation"]
    r = client.post("/api/reports", json={"payload": att["payload"], "sig": att["sig"], "title": "My <b>essay</b>"})
    assert r.status_code == 200
    rid = r.json()["id"]
    assert rid == security.report_id(att["sig"])
    doc = client.get(f"/api/reports/{rid}").json()
    assert doc["payload"] == att["payload"] and "<" not in doc["title"]

    forged = json.loads(att["payload"]); forged["similarity"] = 0.0; forged["ai"] = 1.0
    r = client.post("/api/reports", json={"payload": json.dumps(forged), "sig": att["sig"], "title": "fake"})
    assert r.status_code == 400
    r = client.post("/api/reports", json={"id": "BA-AAAAA-AAAAA", "similarity": 0, "rows": []})  # old unsigned format
    assert r.status_code == 400
    assert client.get("/api/reports/BA-AAAAA-AAAAA").status_code == 404
    assert client.get("/api/reports/../../etc/passwd").status_code == 404


# ----------------------------------------------------------------------------- accounts
def _signup(email, password="pytestpass123", name="Py"):
    r = client.post("/api/auth/signup", json={"name": name, "email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["user"]


def test_auth_flow_and_limits():
    _signup("flow@example.com")
    assert client.get("/api/auth/me").json()["user"]["email"] == "flow@example.com"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.post("/api/auth/login", json={"email": "flow@example.com", "password": "wrong-password"}).status_code == 401
    assert client.post("/api/auth/signup", json={"email": "flow@example.com", "password": "pytestpass123"}).status_code == 409
    assert client.post("/api/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 400
    assert client.post("/api/auth/signup", json={"email": "a@b.co", "password": "x" * 300}).status_code == 400
    assert client.get("/api/scans").status_code == 401


def test_saved_scan_is_sanitised_and_never_contains_essay_text():
    _signup("save@example.com")
    evil = {
        "id": "x", "title": "T<script>alert(1)</script>", "words": 120, "similarity": 12.5, "aiPct": 30,
        "text": "THE WHOLE ESSAY", "grammar": {"open": 2, "kinds": {"spelling": 2}},
        "sources": [{"n": 1, "url": "javascript:alert(1)", "title": "x"}, {"n": 2, "url": "https://example.org/a", "title": "ok", "pct": 5, "color": '"><img src=x onerror=alert(1)>'}],
        "rows": [{"text": "a secret sentence", "src": 2, "ai": 0.4, "excluded": None, "kind": "exact"}],
        "attestation": {"payload": "{}", "sig": "00"}, "extra": {"x": 1},
    }
    sid = client.post("/api/scans", json=evil).json()["id"]
    saved = client.get(f"/api/scans/{sid}").json()
    blob = json.dumps(saved)
    assert "THE WHOLE ESSAY" not in blob and "a secret sentence" not in blob and "<" not in blob and "onerror" not in blob
    assert [s["url"] for s in saved["sources"]] == ["https://example.org/a"]
    assert saved["attestation"] is None and "extra" not in saved and saved["rows"][0] == {"src": 2, "ai": 0.4, "excluded": None, "kind": "exact"}
    assert client.get("/api/scans").json()[0]["similarity"] == 12.5


def test_scans_are_private_to_their_owner_and_account_can_be_deleted():
    _signup("owner@example.com")
    sid = client.post("/api/scans", json={"title": "mine", "words": 10}).json()["id"]
    client.post("/api/auth/logout")
    _signup("other@example.com")
    assert client.get(f"/api/scans/{sid}").status_code == 404
    assert client.delete(f"/api/scans/{sid}").status_code == 404
    assert client.post("/api/auth/delete", json={"password": "not-my-password"}).status_code == 401
    assert client.post("/api/auth/delete", json={"password": "pytestpass123"}).json() == {"deleted": True}
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.post("/api/auth/login", json={"email": "other@example.com", "password": "pytestpass123"}).status_code == 401


def test_document_bank_is_per_owner():
    doc = ("Private course reading. " + ENGLISH).encode()
    _signup("teacher1@example.com")
    assert client.post("/api/corpus", files={"file": ("reading.txt", doc, "text/plain")}).status_code == 200
    mine = client.get("/api/corpus").json()
    assert len(mine) == 1
    client.post("/api/auth/logout")
    _signup("teacher2@example.com")
    assert client.get("/api/corpus").json() == []                                  # cannot see someone else's documents
    assert client.delete(f"/api/corpus/{mine[0]['id']}").status_code == 404        # nor delete them
