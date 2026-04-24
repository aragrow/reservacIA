from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import Settings, get_settings

JWT_ALGO = "HS256"
bearer_scheme = HTTPBearer(auto_error=False)


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        networks = settings.allowed_networks()
        client_host = request.client.host if request.client else None
        if client_host is None:
            return JSONResponse({"detail": "forbidden"}, status_code=status.HTTP_403_FORBIDDEN)
        try:
            addr = ip_address(client_host)
        except ValueError:
            return JSONResponse({"detail": "forbidden"}, status_code=status.HTTP_403_FORBIDDEN)
        if not any(addr in net for net in networks):
            return JSONResponse({"detail": "forbidden"}, status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)


def verify_client_credentials(client_id: str, client_secret: str, settings: Settings) -> bool:
    id_ok = hmac.compare_digest(client_id.encode(), settings.client_id.encode())
    secret_ok = hmac.compare_digest(client_secret.encode(), settings.client_secret.encode())
    return id_ok and secret_ok


TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def _issue_token(settings: Settings, ttl_seconds: int, token_type: str) -> tuple[str, int]:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": settings.client_id,
        "cid": settings.client_id,
        "typ": token_type,
        "jti": secrets.token_hex(8),  # unique per token, even within the same second
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGO)
    return token, ttl_seconds


def create_access_token(settings: Settings) -> tuple[str, int]:
    return _issue_token(settings, settings.jwt_ttl_minutes * 60, TOKEN_TYPE_ACCESS)


def create_refresh_token(settings: Settings) -> tuple[str, int]:
    return _issue_token(settings, settings.refresh_ttl_days * 86400, TOKEN_TYPE_REFRESH)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode(settings: Settings, token: str, expected_type: str) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise _unauthorized("token expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("invalid token")
    if claims.get("typ") != expected_type:
        raise _unauthorized(f"expected {expected_type} token")
    cid = claims.get("cid")
    if not isinstance(cid, str) or not hmac.compare_digest(cid.encode(), settings.client_id.encode()):
        raise _unauthorized("client id mismatch")
    return claims


def verify_refresh_token(settings: Settings, token: str) -> dict:
    return _decode(settings, token, TOKEN_TYPE_REFRESH)


def require_agent(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> str:
    # The JWT is signed with JWT_SECRET and carries a `cid` claim. _decode
    # verifies signature, expiry, token type, and that `cid` matches the
    # configured CLIENT_ID — so identity is fully established from the Bearer
    # token alone. No separate client-id header needed.
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("missing bearer token")
    claims = _decode(settings, credentials.credentials, TOKEN_TYPE_ACCESS)
    return claims["cid"]
