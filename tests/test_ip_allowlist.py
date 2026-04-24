from __future__ import annotations

from fastapi.testclient import TestClient


def test_blocked_from_non_allowlisted_ip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))

    from app.config import get_settings
    get_settings.cache_clear()
    from app import main as app_main
    from app.db import init_db
    init_db()

    with TestClient(app_main.app, client=("203.0.113.99", 55555)) as tc:
        # Even /health is IP-gated.
        assert tc.get("/health").status_code == 403
        # Token endpoint is IP-gated too.
        resp = tc.post(
            "/auth/token",
            json={"client_id": "test-client", "client_secret": "test-secret"},
        )
        assert resp.status_code == 403
    get_settings.cache_clear()


def test_allowed_from_allowlisted_ip(client):
    assert client.get("/health").status_code == 200
