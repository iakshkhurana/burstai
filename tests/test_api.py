"""API smoke tests with FastAPI's TestClient - no network needed for the parts asserted here.
Catches wiring mistakes (NameError etc.) that unit tests on the engines cannot see.

    .venv/Scripts/python -m pytest tests/test_api.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["BURSTAI_PRELOAD"] = "0"
os.environ["BURSTAI_ENGINE_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_and_pages():
    assert client.get("/api/health").status_code == 200
    for p in ("/", "/check", "/report", "/login", "/signup", "/me", "/robots.txt", "/sitemap.xml"):
        assert client.get(p).status_code == 200, p


def test_check_without_network_parts_does_not_500():
    r = client.post("/api/check", json={"text": "This is a simple test sentence that should be analysed. " * 4,
                                         "options": {"plagiarism": False, "grammar": False, "ai": True, "llm_judge": False, "gemini_key": ""}})
    assert r.status_code == 200, r.text
    assert "ai" in r.json()


def test_gemini_validate_rejects_empty():
    r = client.post("/api/gemini/validate", json={"key": ""})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_auth_flow():
    email = "pytest-user@example.com"
    client.post("/api/auth/signup", json={"name": "Py", "email": email, "password": "pytestpass123"})
    r = client.post("/api/auth/login", json={"email": email, "password": "pytestpass123"})
    assert r.status_code == 200 and r.json()["user"]["email"] == email
    assert client.get("/api/auth/me").json()["user"]["email"] == email
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
