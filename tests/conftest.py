from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def env_setup() -> Iterator[None]:
    tmpdir = tempfile.mkdtemp(prefix="reservacia-tests-")
    db_path = str(Path(tmpdir) / "session.db")
    os.environ.update(
        {
            "DATABASE_PATH": db_path,
            "JWT_SECRET": "test-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "JWT_TTL_MINUTES": "60",
            "CLIENT_ID": "test-client",
            "CLIENT_SECRET": "test-secret",
            "ALLOWED_IPS": "127.0.0.1/32",
        }
    )
    yield


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    from app.config import get_settings
    get_settings.cache_clear()

    from app import main as app_main
    from app.db import connection, init_db
    init_db()
    # Seed the 50-table layout so reservation tests can auto-assign.
    from scripts.seed_tables import ensure_tables
    with connection() as conn:
        ensure_tables(conn)

    with TestClient(app_main.app, client=("127.0.0.1", 12345)) as tc:
        yield tc
    get_settings.cache_clear()


@pytest.fixture
def token(client: TestClient) -> str:
    resp = client.post(
        "/auth/token",
        json={"client_id": "test-client", "client_secret": "test-secret"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
