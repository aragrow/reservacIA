"""Security middlewares: audit log, rate limit, security headers, body-size cap.

Wired in app/main.py. The IP allowlist middleware lives in app/security.py for
historical reasons.

Design notes
------------
- All four use BaseHTTPMiddleware to match IPAllowlistMiddleware's style.
- Rate-limit and audit both pull `cid` from the JWT *without verifying the
  signature* — that's intentional. They're for grouping/forensics only;
  the actual auth check still runs in `require_agent`. Logging an attacker's
  forged cid is a feature, not a bug.
- The audit log is append-only JSONL at `data/audit.jsonl`. Mutations
  (POST/PATCH on data routes), every `/auth/*` outcome, and any 401 anywhere
  generate a row. GETs are not logged to keep volume sane.
- Rate limits use a sliding window in process memory. Single-instance only;
  if you ever scale horizontally swap the bucket store for Redis.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings


# ---------------------------------------------------------- helpers

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _cid_unverified(request: Request) -> Optional[str]:
    """Best-effort cid extraction. Used only for grouping — never for auth."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        return claims.get("cid")
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------- security headers

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defensive HTTP headers to every response.

    Notes:
    - No CSP here. /docs (Swagger UI) needs inline scripts to render; a strict
      CSP would break it. If you ever ship a real /app/* surface, add a
      route-scoped CSP there separately.
    - HSTS is only meaningful behind HTTPS. We set it anyway; browsers ignore
      it on plain HTTP, so it's safe.
    """
    HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for name, value in self.HEADERS.items():
            response.headers.setdefault(name, value)
        return response


# ---------------------------------------------------------- body size cap

class BodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured cap.

    Streamed/chunked uploads without Content-Length are allowed through; FastAPI
    will fail them at deserialization time anyway since this API only accepts
    small JSON bodies.
    """
    async def dispatch(self, request: Request, call_next):
        cap = get_settings().max_body_bytes
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > cap:
                    return JSONResponse(
                        {"detail": f"request body exceeds {cap} bytes"},
                        status_code=413,
                    )
            except ValueError:
                return JSONResponse(
                    {"detail": "invalid content-length"}, status_code=400
                )
        return await call_next(request)


# ---------------------------------------------------------- rate limit

class _SlidingWindow:
    """In-process per-key sliding window. Thread-safety isn't needed because
    Starlette's request handling is async-cooperative (no preemption between
    awaits when we're just touching a dict)."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: int, window_seconds: float) -> Optional[int]:
        """Record a hit. Returns Retry-After seconds if rate-limited, else None."""
        now = time.monotonic()
        bucket = self._buckets[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = int(window_seconds - (now - bucket[0])) + 1
            return max(1, retry)
        bucket.append(now)
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit, partitioned by route bucket and identifier.

    Buckets:
      - "auth_token": /auth/token  → per-IP, strictest (brute-force throttle)
      - "auth_other": /auth/refresh, /_debug/*  → per-IP-or-cid
      - "data":       /reservations, /rooms, /tables, /reviews → per-cid
      - "free":       /health, /docs, /openapi.json, /app/* → unlimited
    """
    _store = _SlidingWindow()

    DATA_PREFIXES = ("/reservations", "/rooms", "/tables", "/reviews")
    FREE_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/app", "/static")

    def _classify(self, path: str) -> str:
        if path == "/auth/token":
            return "auth_token"
        if path.startswith("/auth/") or path.startswith("/_debug/"):
            return "auth_other"
        if any(path.startswith(p) for p in self.DATA_PREFIXES):
            return "data"
        if any(path.startswith(p) for p in self.FREE_PREFIXES):
            return "free"
        return "data"  # default: treat unknowns as data

    async def dispatch(self, request: Request, call_next):
        bucket = self._classify(request.url.path)
        if bucket == "free":
            return await call_next(request)

        settings = get_settings()
        if bucket == "auth_token":
            limit = settings.rate_limit_auth_per_minute
            ident = _client_ip(request)
        elif bucket == "auth_other":
            limit = settings.rate_limit_other_per_minute
            ident = _cid_unverified(request) or _client_ip(request)
        else:  # data
            limit = settings.rate_limit_data_per_minute
            ident = _cid_unverified(request) or _client_ip(request)

        retry = self._store.hit(f"{bucket}:{ident}", limit, 60.0)
        if retry is not None:
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        return await call_next(request)


# ---------------------------------------------------------- audit log

class AuditLogMiddleware(BaseHTTPMiddleware):
    """Append-only JSONL of mutations + every `/auth/*` outcome + any 401.

    Forensic record. Never blocks a request; logging errors are swallowed so
    a corrupt FS or full disk can't bring the API down.
    """
    MUTATING_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

    def _classify(self, request: Request, status_code: int) -> Optional[str]:
        path = request.url.path
        method = request.method
        if path.startswith("/auth/"):
            return "auth_success" if 200 <= status_code < 300 else "auth_failure"
        if status_code == 401:
            return "auth_failure"
        if method in self.MUTATING_METHODS and not path.startswith("/health"):
            return "mutation"
        return None

    def _write(self, payload: dict) -> None:
        try:
            path = Path(get_settings().audit_log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            # Audit log MUST NOT break the request path.
            pass

    async def dispatch(self, request: Request, call_next):
        # Capture body length without consuming it (for forensics) — we only
        # peek at content-length, never the bytes.
        body_len = request.headers.get("content-length")
        response: Response = await call_next(request)
        event = self._classify(request, response.status_code)
        if event is None:
            return response
        # Lightweight tamper-evidence: hash of UA + IP + path; not a content hash.
        fp = hashlib.sha256(
            (
                request.headers.get("user-agent", "")
                + "|" + _client_ip(request)
                + "|" + request.url.path
            ).encode()
        ).hexdigest()[:16]
        self._write({
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "event": event,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ip": _client_ip(request),
            "cid": _cid_unverified(request),
            "body_bytes": int(body_len) if body_len and body_len.isdigit() else None,
            "fp": fp,
        })
        return response
