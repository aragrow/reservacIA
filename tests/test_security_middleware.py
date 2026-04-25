from __future__ import annotations

import json

from fastapi.testclient import TestClient


# ---------------------------------------------------------- helpers

def _fresh_client(tmp_path, monkeypatch, **env_overrides) -> TestClient:
    """Build a TestClient with overridable env. Used by tests that need to flip
    a limit lower than the very-high defaults set in conftest."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, str(v))

    from app.config import get_settings
    get_settings.cache_clear()

    from app import main as app_main
    from app.db import init_db
    from app.middleware import RateLimitMiddleware
    init_db()
    RateLimitMiddleware._store._buckets.clear()

    return TestClient(app_main.app, client=("127.0.0.1", 12345))


# ---------------------------------------------------------- security headers

def test_security_headers_on_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "noindex" in resp.headers["x-robots-tag"]


def test_security_headers_on_auth_failure(client):
    # 401 responses still get the headers — they go through the same outer
    # middleware.
    resp = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "wrong"},
    )
    assert resp.status_code == 401
    assert resp.headers["x-content-type-options"] == "nosniff"


# ---------------------------------------------------------- body size

def test_body_size_cap_rejects_oversized(tmp_path, monkeypatch):
    tc = _fresh_client(tmp_path, monkeypatch, MAX_BODY_BYTES="1024")
    big = "x" * 2000
    resp = tc.post(
        "/auth/token",
        # content-length will be > 1024
        json={"client_id": big, "client_secret": "y"},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"]


def test_body_size_cap_allows_small(tmp_path, monkeypatch):
    tc = _fresh_client(tmp_path, monkeypatch, MAX_BODY_BYTES="65536")
    resp = tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------- rate limit

def test_rate_limit_throttles_auth_token(tmp_path, monkeypatch):
    """5 bad attempts allowed, 6th gets 429 with Retry-After."""
    tc = _fresh_client(tmp_path, monkeypatch, RATE_LIMIT_AUTH_PER_MINUTE="5")
    bad = {"client_id": "test-client", "client_secret": "wrong"}
    for _ in range(5):
        resp = tc.post("/auth/token", json=bad)
        assert resp.status_code == 401
    resp = tc.post("/auth/token", json=bad)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1


def test_rate_limit_doesnt_throttle_health(tmp_path, monkeypatch):
    """`/health` is in the FREE bucket — no limit even at 1/min."""
    tc = _fresh_client(tmp_path, monkeypatch, RATE_LIMIT_DATA_PER_MINUTE="1")
    for _ in range(10):
        assert tc.get("/health").status_code == 200


def test_rate_limit_data_routes(tmp_path, monkeypatch):
    """Data routes use a per-cid bucket; trip it after N requests."""
    tc = _fresh_client(tmp_path, monkeypatch, RATE_LIMIT_DATA_PER_MINUTE="3")
    from app.db import connection
    from scripts.seed_tables import ensure_tables
    with connection() as conn:
        ensure_tables(conn)
    # Get a token (auth bucket is unlimited in this fixture).
    pair = tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {pair['access_token']}"}
    for _ in range(3):
        assert tc.get("/reservations", headers=headers).status_code == 200
    resp = tc.get("/reservations", headers=headers)
    assert resp.status_code == 429


# ---------------------------------------------------------- audit log

def test_audit_log_records_auth_outcomes(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    tc = _fresh_client(tmp_path, monkeypatch)  # AUDIT_LOG_PATH set inside

    tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "wrong"},
    )
    tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    )

    rows = [json.loads(l) for l in audit.read_text().splitlines()]
    events = [r["event"] for r in rows]
    assert "auth_failure" in events
    assert "auth_success" in events
    # Required fields on every row.
    for r in rows:
        assert {"ts", "event", "method", "path", "status", "ip", "fp"} <= r.keys()


def test_audit_log_records_mutations(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    tc = _fresh_client(tmp_path, monkeypatch)

    from app.db import connection
    from scripts.seed_tables import ensure_tables
    with connection() as conn:
        ensure_tables(conn)

    pair = tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {pair['access_token']}"}

    # GET should NOT be logged.
    tc.get("/reservations", headers=headers)
    # POST should be logged as a mutation.
    tc.post(
        "/reviews",
        json={"reviewer_name": "x", "rating": 5, "body": "great"},
        headers=headers,
    )

    rows = [json.loads(l) for l in audit.read_text().splitlines()]
    mutations = [r for r in rows if r["event"] == "mutation"]
    assert len(mutations) >= 1
    assert any(r["method"] == "POST" and r["path"] == "/reviews" for r in mutations)
    # No mutation row for the GET.
    assert not any(r["method"] == "GET" and r["event"] == "mutation" for r in rows)


def test_audit_log_records_cid_when_present(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    tc = _fresh_client(tmp_path, monkeypatch)

    from app.db import connection
    from scripts.seed_tables import ensure_tables
    with connection() as conn:
        ensure_tables(conn)

    pair = tc.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    ).json()
    headers = {"Authorization": f"Bearer {pair['access_token']}"}
    tc.post(
        "/reviews",
        json={"reviewer_name": "y", "rating": 4, "body": "ok"},
        headers=headers,
    )

    rows = [json.loads(l) for l in audit.read_text().splitlines()]
    mut = next(r for r in rows if r["event"] == "mutation" and r["path"] == "/reviews")
    assert mut["cid"] == "test-client"
