"""Accounts, sessions and saved scans - plain SQLite, PBKDF2 password hashing, HttpOnly cookie sessions.

No third-party service is involved. The same endpoints are mirrored by the Cloudflare Worker
(cloudflare/src/worker.js) on top of D1 so the front-end does not care where it is hosted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from . import config

_DB = config.DATA_DIR / "users.sqlite3"
COOKIE = "burstai_session"
SESSION_TTL = 30 * 86400
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ITER = 210_000


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            pw_hash TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, report_id TEXT, title TEXT,
            words INTEGER, similarity REAL, ai REAL, grammar INTEGER, report TEXT, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS scans_user ON scans(user_id, created DESC);
        """
    )
    return c


@dataclass
class User:
    id: int
    email: str
    name: str
    created: float

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "initial": (self.name or self.email)[:1].upper(), "created": self.created}


# ------------------------------------------------------------------ passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, _ITER)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(got.hex(), dk)
    except Exception:  # noqa: BLE001
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------ accounts
def signup(name: str, email: str, password: str) -> User:
    name, email = name.strip()[:80], email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "Enter a valid email address")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not name:
        name = email.split("@")[0]
    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists - log in instead")
        cur = c.execute("INSERT INTO users (email, name, pw_hash, created) VALUES (?,?,?,?)", (email, name, hash_password(password), time.time()))
        return User(cur.lastrowid, email, name, time.time())


def login(email: str, password: str) -> User:
    email = email.strip().lower()
    with _conn() as c:
        row = c.execute("SELECT id, email, name, pw_hash, created FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(password, row[3]):
        raise HTTPException(401, "Wrong email or password")
    return User(row[0], row[1], row[2], row[4])


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO sessions (token_hash, user_id, expires) VALUES (?,?,?)", (_token_hash(token), user_id, time.time() + SESSION_TTL))
    return token


def destroy_session(token: str | None) -> None:
    if token:
        with _conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def current_user(request: Request) -> User | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT u.id, u.email, u.name, u.created FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=? AND s.expires > ?",
            (_token_hash(token), time.time()),
        ).fetchone()
    return User(*row) if row else None


def require_user(request: Request) -> User:
    u = current_user(request)
    if not u:
        raise HTTPException(401, "Please log in")
    return u


def set_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or bool(config.PUBLIC_URL.startswith("https"))
    response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax", secure=secure, path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ------------------------------------------------------------------ saved scans
def save_scan(user_id: int, report: dict) -> dict:
    report = dict(report)
    report.pop("text", None)  # the essay itself is never stored
    row = (
        user_id,
        str(report.get("id") or ""),
        str(report.get("title") or "Untitled")[:160],
        int(report.get("words") or 0),
        float(report.get("similarity") or 0),
        None if report.get("aiPct") is None else float(report["aiPct"]),
        None if not report.get("grammar") else int(report["grammar"].get("open", 0)),
        json.dumps(report, ensure_ascii=False),
        time.time(),
    )
    with _conn() as c:
        # one row per report id per user; re-saving updates it
        c.execute("DELETE FROM scans WHERE user_id=? AND report_id=? AND report_id<>''", (user_id, row[1]))
        cur = c.execute(
            "INSERT INTO scans (user_id, report_id, title, words, similarity, ai, grammar, report, created) VALUES (?,?,?,?,?,?,?,?,?)", row
        )
        return {"id": cur.lastrowid}


def list_scans(user_id: int, limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, report_id, title, words, similarity, ai, grammar, created FROM scans WHERE user_id=? ORDER BY created DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    keys = ["id", "report_id", "title", "words", "similarity", "ai", "grammar", "created"]
    return [dict(zip(keys, r)) for r in rows]


def get_scan(user_id: int, scan_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT report FROM scans WHERE user_id=? AND id=?", (user_id, scan_id)).fetchone()
    return json.loads(row[0]) if row else None


def delete_scan(user_id: int, scan_id: int) -> bool:
    with _conn() as c:
        return c.execute("DELETE FROM scans WHERE user_id=? AND id=?", (user_id, scan_id)).rowcount > 0


def bootstrap_admin() -> None:
    """Optional: BURSTAI_ADMIN_EMAIL / BURSTAI_ADMIN_PASSWORD create a first account on start."""
    email, pw = os.getenv("BURSTAI_ADMIN_EMAIL", ""), os.getenv("BURSTAI_ADMIN_PASSWORD", "")
    if email and pw:
        try:
            signup("Admin", email, pw)
        except HTTPException:
            pass
