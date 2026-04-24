from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.config import Settings, get_settings
from app.models import RefreshRequest, TokenRequest, TokenResponse
from app.security import (
    create_access_token,
    create_refresh_token,
    verify_client_credentials,
    verify_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# When local_mode=true, pre-fill the Swagger "Try it out" form with the actual
# .env values so the reviewer can just hit Execute.
_s = get_settings()
_TOKEN_EXAMPLE = (
    {"client_id": _s.client_id, "client_secret": _s.client_secret}
    if _s.local_mode else
    {"client_id": "agent-client", "client_secret": "your-client-secret"}
)
_REFRESH_EXAMPLE = (
    {"refresh_token": _s.refresh_token}
    if _s.local_mode and _s.refresh_token else
    {"refresh_token": "<paste a refresh_token from POST /auth/token>"}
)


def _pair(settings: Settings) -> TokenResponse:
    access, access_ttl = create_access_token(settings)
    refresh, refresh_ttl = create_refresh_token(settings)
    return TokenResponse(
        access_token=access,
        expires_in=access_ttl,
        refresh_token=refresh,
        refresh_expires_in=refresh_ttl,
    )


@router.post("/token", response_model=TokenResponse)
def issue_token(
    body: TokenRequest = Body(..., examples=[_TOKEN_EXAMPLE]),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if not verify_client_credentials(body.client_id, body.client_secret, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid client credentials",
        )
    return _pair(settings)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    body: RefreshRequest = Body(..., examples=[_REFRESH_EXAMPLE]),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    # Verifies signature, expiry, `typ=refresh`, and matching client id.
    # On success, issues a fresh access token AND a fresh refresh token
    # (rotating refresh) so the 6-month window keeps sliding as long as
    # the agent refreshes at least once every 6 months.
    verify_refresh_token(settings, body.refresh_token)
    return _pair(settings)
