from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt


def test_token_issued_for_valid_creds(client):
    resp = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 60 * 60     # 60 min TTL in test env
    assert isinstance(body["access_token"], str)
    # 180-day refresh token (default) — test env doesn't override it.
    assert body["refresh_expires_in"] == 180 * 86400
    assert isinstance(body["refresh_token"], str)
    assert body["refresh_token"] != body["access_token"]


def test_refresh_endpoint_issues_new_pair(client):
    initial = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    ).json()

    resp = client.post(
        "/auth/refresh",
        json={"refresh_token": initial["refresh_token"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] and body["access_token"] != initial["access_token"]
    # Rotating refresh tokens — new one returned each time.
    assert body["refresh_token"] and body["refresh_token"] != initial["refresh_token"]
    assert body["expires_in"] == 60 * 60


def test_refresh_rejects_access_token_as_refresh(client, token):
    # The 'token' fixture is an access token — it must not be usable to refresh.
    resp = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
    assert "expected refresh" in resp.json()["detail"]


def test_protected_route_rejects_refresh_token_as_bearer(client):
    pair = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    ).json()
    resp = client.get(
        "/reservations",
        headers={"Authorization": f"Bearer {pair['refresh_token']}"},
    )
    assert resp.status_code == 401
    assert "expected access" in resp.json()["detail"]


def test_token_rejected_for_bad_creds(client):
    resp = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "wrong"},
    )
    assert resp.status_code == 401


def test_token_rejected_for_unknown_client(client):
    resp = client.post(
        "/auth/token",
        json={"client_id": "other", "client_secret": "test-secret"},
    )
    assert resp.status_code == 401


def test_protected_route_requires_bearer(client):
    resp = client.get("/reservations")
    assert resp.status_code == 401


def test_protected_route_rejects_forged_cid_claim(client):
    # A JWT signed with our secret but claiming to be a different client must
    # be rejected — identity is enforced via the cid claim, not a header.
    from app.config import get_settings
    settings = get_settings()
    forged = jwt.encode(
        {
            "sub": "someone-else",
            "cid": "someone-else",
            "typ": "access",
            "iat": int(datetime.now(tz=timezone.utc).timestamp()),
            "exp": int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/reservations", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    assert "client id mismatch" in resp.json()["detail"]


def test_protected_route_rejects_expired_token(client):
    from app.config import get_settings
    settings = get_settings()
    expired = jwt.encode(
        {
            "sub": settings.client_id,
            "cid": settings.client_id,
            "typ": "access",
            "iat": int((datetime.now(tz=timezone.utc) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(tz=timezone.utc) - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = client.get("/reservations", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_protected_route_rejects_wrong_signature(client):
    forged = jwt.encode(
        {"sub": "test-client", "cid": "test-client", "typ": "access", "exp": 9999999999},
        "not-the-right-secret",
        algorithm="HS256",
    )
    resp = client.get("/reservations", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
