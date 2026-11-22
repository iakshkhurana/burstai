"""Cross-cutting safety helpers: rate limiting, signed result attestations, SSRF guard,
client identification behind the Worker proxy, security headers."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import threading
import time
import unicodedata
from collections import deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from . import config

# ----------------------------------------------------------------------------- rate limiting
class RateLimiter:
    """In-memory sliding-window limiter. Good enough for a single engine process."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> int:
        """Record a hit. Returns 0 if allowed, else seconds until the next slot frees up."""
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 20000:  # crude memory cap
                self._hits = {k: q for k, q in self._hits.items() if q and now - q[-1] < 3600}
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(window - (now - q[0])) + 1)
            q.append(now)
            return 0


LIMITER = RateLimiter()


def trusted_proxy(request: Request) -> bool:
    """True when the request carries the shared engine token (i.e. it came through our Worker)."""
    return bool(config.ENGINE_TOKEN) and hmac.compare_digest(request.headers.get("x-engine-token", ""), config.ENGINE_TOKEN)


def client_ip(request: Request) -> str:
    if trusted_proxy(request):
        fwd = request.headers.get("x-client-ip", "").strip()
        if fwd:
            return fwd[:64]
    return request.client.host if request.client else "unknown"


def limit(request: Request, bucket: str, n: int, window: float) -> None:
    wait = LIMITER.check(f"{bucket}:{client_ip(request)}", n, window)
    if wait:
        mins = max(1, round(wait / 60))
        raise HTTPException(
            429,
            f"Too many requests. Please try again in about {mins} minute{'s' if mins != 1 else ''} - limits keep BurstAI free for everyone.",
            headers={"Retry-After": str(wait)},
        )


# ----------------------------------------------------------------------------- attestations
_SECRET: bytes | None = None


def _secret() -> bytes:
    """HMAC key for result attestations: the engine token when set (so the Worker can verify),
    otherwise a random key persisted in the data directory."""
    global _SECRET
    if _SECRET is None:
        if config.ENGINE_TOKEN:
            _SECRET = config.ENGINE_TOKEN.encode()
        else:
            path = config.DATA_DIR / "report.key"
            if not path.exists():
                path.write_text(secrets.token_hex(32), encoding="utf-8")
            _SECRET = path.read_text(encoding="utf-8").strip().encode()
    return _SECRET


def text_hash(text: str) -> str:
    """SHA-256 of the whitespace/Unicode-normalised text. The browser computes the same value
    (NFC, collapse whitespace, trim) so a teacher can confirm a document matches a report."""
    norm = " ".join(unicodedata.normalize("NFC", text).split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def attest(data: dict) -> dict:
    """Serialise *data* canonically and sign it. Verifiers HMAC the exact payload string."""
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sig = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"payload": payload, "sig": sig}


def verify_attestation(payload: str, sig: str) -> bool:
    if not isinstance(payload, str) or not isinstance(sig, str) or len(payload) > 20000:
        return False
    good = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def report_id(sig: str) -> str:
    s = sig.upper()
    return f"BA-{s[:5]}-{s[5:10]}"


# ----------------------------------------------------------------------------- SSRF guard
def is_public_url(url: str) -> bool:
    """Only fetch http(s) URLs whose host resolves exclusively to public addresses."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        host = u.hostname
        if host in ("localhost",) or host.endswith((".local", ".internal", ".lan")):
            return False
        infos = socket.getaddrinfo(host, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except Exception:  # noqa: BLE001 - unresolvable or malformed
        return False


# ----------------------------------------------------------------------------- headers
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
