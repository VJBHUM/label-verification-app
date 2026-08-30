"""Lightweight access control for the deployed prototype.

A single shared passcode gates the app so that a public URL backed by a paid API
key isn't open to the world. On success we issue a signed, HttpOnly session
cookie (HMAC-SHA256, with an expiry) — no server-side session store needed.

This is deliberately minimal: one shared code, not per-user accounts. It exists
to stop strangers from spending API credits, not to satisfy federal identity
requirements (that would be PIV/SSO in a real deployment).

If ``APP_ACCESS_CODE`` is unset, the gate is disabled (open) and a warning is
logged — convenient for local development, but every real deployment must set it.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time

logger = logging.getLogger("label_verification.auth")

SESSION_COOKIE = "lv_session"
SESSION_TTL_SECONDS = int(os.environ.get("APP_SESSION_TTL", str(8 * 3600)))

ACCESS_CODE = os.environ.get("APP_ACCESS_CODE", "")

# Signing secret for session cookies. If unset we generate a random per-process
# secret: sessions simply won't survive a restart or span multiple instances.
_SECRET = os.environ.get("APP_SECRET_KEY")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    if ACCESS_CODE:
        logger.warning("APP_SECRET_KEY not set — using an ephemeral secret; sessions won't survive restarts.")
_SECRET_BYTES = _SECRET.encode("utf-8")

if not ACCESS_CODE:
    logger.warning("APP_ACCESS_CODE not set — the access gate is DISABLED. Set it before deploying publicly.")


def auth_enabled() -> bool:
    return bool(ACCESS_CODE)


def check_passcode(supplied: str) -> bool:
    """Constant-time comparison of the supplied passcode."""
    if not ACCESS_CODE:
        return True
    return hmac.compare_digest((supplied or "").encode("utf-8"), ACCESS_CODE.encode("utf-8"))


def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_SECRET_BYTES, payload, hashlib.sha256).digest()).decode("ascii")


def create_session_token(ttl: int = SESSION_TTL_SECONDS) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + ttl}).encode("utf-8")
    ).decode("ascii")
    return f"{payload}.{_sign(payload.encode('ascii'))}"


def verify_session_token(token: str) -> bool:
    """Validate signature (constant-time) and expiry."""
    if not token or "." not in token:
        return False
    payload, sig = token.split(".", 1)
    expected = _sign(payload.encode("ascii"))
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return False
    return isinstance(data, dict) and float(data.get("exp", 0)) > time.time()


# --- Per-client rate limiting (in-process, fixed window) ---


class FixedWindowLimiter:
    """A simple per-key fixed-window limiter.

    In-process only: correct for a single instance. A multi-instance production
    deployment would back this with Redis or the platform's edge rate limiter.
    """

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[tuple[str, int], int] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        window = int(now // self.window)
        # Opportunistic cleanup of stale windows.
        if len(self._hits) > 10000:
            self._hits = {k: v for k, v in self._hits.items() if k[1] >= window}
        bucket = (key, window)
        count = self._hits.get(bucket, 0)
        if count >= self.limit:
            return False
        self._hits[bucket] = count + 1
        return True


def client_ip(request) -> str:
    """Best-effort client IP, honoring a single proxy hop (X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
