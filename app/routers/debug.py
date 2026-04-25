"""Local-development convenience endpoints.

All endpoints here require `LOCAL_MODE=true` in .env AND a request from a
loopback IP (127.0.0.1 or ::1). They are explicitly NOT safe for production.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security import create_access_token, create_refresh_token

router = APIRouter(prefix="/_debug", tags=["debug"], include_in_schema=False)

_LOOPBACK = {"127.0.0.1", "::1"}


def _guard(request: Request, settings: Settings) -> None:
    if not settings.local_mode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    host = request.client.host if request.client else None
    if host not in _LOOPBACK:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.get("/dev-token")
def dev_token(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Issue a fresh access+refresh pair without requiring credentials.

    Used by the auto-auth script in /docs so opening Swagger locally
    works without the copy-paste-a-token dance.
    """
    _guard(request, settings)
    access, access_ttl, access_exp = create_access_token(settings)
    refresh, refresh_ttl, refresh_exp = create_refresh_token(settings)
    return {
        "access_token": access,
        "expires_in": access_ttl,
        "expires_at": access_exp.strftime(settings.datetime_format),
        "refresh_token": refresh,
        "refresh_expires_in": refresh_ttl,
        "refresh_expires_at": refresh_exp.strftime(settings.datetime_format),
        "token_type": "bearer",
    }
