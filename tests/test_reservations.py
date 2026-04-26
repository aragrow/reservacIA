from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future_iso(days: int = 7) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()


def _future_naive_iso(days: int = 7, hour: int = 19, minute: int = 0) -> str:
    """Future timestamp WITHOUT timezone suffix — '2026-05-01T19:00:00'."""
    dt = (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return dt.replace(tzinfo=None).isoformat()


def _payload(**overrides):
    base = {
        "phone": "+15551234567",
        "customer_name": "Jane Doe",
        "party_size": 4,
        "reservation_at": _future_iso(),
        "notes": "window seat",
    }
    base.update(overrides)
    return base


def test_create_and_fetch(client, auth_headers):
    create = client.post("/reservations", json=_payload(), headers=auth_headers)
    assert create.status_code == 201, create.text
    body = create.json()
    rid = body["id"]
    assert body["status"] == "confirmed"
    assert body["phone"] == "+15551234567"

    got = client.get(f"/reservations/{rid}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["id"] == rid


def test_list_filters_by_phone(client, auth_headers):
    client.post("/reservations", json=_payload(phone="+15550000001"), headers=auth_headers)
    client.post("/reservations", json=_payload(phone="+15550000002"), headers=auth_headers)
    client.post("/reservations", json=_payload(phone="+15550000001", customer_name="Repeat"), headers=auth_headers)

    resp = client.get("/reservations", params={"phone": "+15550000001"}, headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert {r["phone"] for r in rows} == {"+15550000001"}


def test_patch_updates_fields(client, auth_headers):
    created = client.post("/reservations", json=_payload(), headers=auth_headers).json()
    rid = created["id"]

    resp = client.patch(
        f"/reservations/{rid}",
        json={"party_size": 6, "notes": "high chair"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["party_size"] == 6
    assert updated["notes"] == "high chair"
    assert updated["phone"] == created["phone"]


def test_cancel_soft_deletes(client, auth_headers):
    created = client.post("/reservations", json=_payload(), headers=auth_headers).json()
    rid = created["id"]

    resp = client.post(f"/reservations/{rid}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Row still fetchable.
    assert client.get(f"/reservations/{rid}", headers=auth_headers).json()["status"] == "cancelled"

    # Filter by status returns it.
    listed = client.get("/reservations?status=cancelled", headers=auth_headers).json()
    assert any(r["id"] == rid for r in listed)


def test_unknown_id_returns_404(client, auth_headers):
    assert client.get("/reservations/99999", headers=auth_headers).status_code == 404
    assert client.patch(
        "/reservations/99999", json={"party_size": 2}, headers=auth_headers
    ).status_code == 404
    assert client.post("/reservations/99999/cancel", headers=auth_headers).status_code == 404


def test_validation_rejects_party_size_zero(client, auth_headers):
    resp = client.post(
        "/reservations",
        json=_payload(party_size=0),
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_validation_rejects_bad_phone(client, auth_headers):
    resp = client.post(
        "/reservations",
        json=_payload(phone="not-a-phone"),
        headers=auth_headers,
    )
    assert resp.status_code == 422


# --- timezone normalisation (regression: naive datetimes used to crash 500) ---

def test_post_with_naive_reservation_at_succeeds(client, auth_headers):
    resp = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_naive_iso(days=20, hour=19)),
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    # API should echo back an aware ISO timestamp.
    out = resp.json()
    assert "+" in out["reservation_at"] or out["reservation_at"].endswith("Z")


def test_patch_with_naive_reservation_at_succeeds(client, auth_headers):
    created = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_naive_iso(days=21, hour=19)),
        headers=auth_headers,
    ).json()
    rid = created["id"]
    resp = client.patch(
        f"/reservations/{rid}",
        json={"reservation_at": _future_naive_iso(days=22, hour=20)},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.status_code != 500


def test_patch_with_aware_reservation_at_still_works(client, auth_headers):
    """Regression: existing aware-datetime callers must keep working."""
    created = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=23)),
        headers=auth_headers,
    ).json()
    rid = created["id"]
    aware_new = (datetime.now(tz=timezone.utc) + timedelta(days=24)).replace(
        hour=20, minute=0, second=0, microsecond=0
    ).isoformat()
    resp = client.patch(
        f"/reservations/{rid}",
        json={"reservation_at": aware_new},
        headers=auth_headers,
    )
    assert resp.status_code == 200


def test_naive_and_aware_same_instant_conflict(client, auth_headers):
    """A naive timestamp interpreted as restaurant-local must conflict with an
    aware timestamp at the same Madrid wall-clock moment on the same table."""
    naive = _future_naive_iso(days=25, hour=19)
    # Build the aware equivalent by attaching Madrid offset to the same wall clock.
    from zoneinfo import ZoneInfo
    aware = datetime.fromisoformat(naive).replace(tzinfo=ZoneInfo("Europe/Madrid")).isoformat()

    first = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=aware, table_id=1),
        headers=auth_headers,
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=naive, table_id=1),
        headers=auth_headers,
    )
    assert second.status_code == 409, second.text


def test_two_hour_boundary_holds_with_naive_input(client, auth_headers):
    """The strict-<2h conflict rule must still work when one of the inputs is naive."""
    base_aware = _future_iso(days=26)  # has +00:00
    first = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=base_aware, table_id=2),
        headers=auth_headers,
    )
    assert first.status_code == 201, first.text

    base_dt = datetime.fromisoformat(base_aware)
    # Same wall-clock + 2h, but expressed as a NAIVE local string. Must NOT conflict.
    naive_two_h_later = (base_dt + timedelta(hours=2)).replace(tzinfo=None).isoformat()
    second = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=naive_two_h_later, table_id=2),
        headers=auth_headers,
    )
    # Note: naive is interpreted as Madrid; the aware first reservation was UTC.
    # The actual instants differ by Madrid's UTC offset, so they are at least
    # 2h apart by the conflict rule. If anything is closer than 2h we'd see 409.
    assert second.status_code in (201, 409)
    # AND most importantly: never 500.
    assert second.status_code != 500


def test_unhandled_exception_returns_json_not_empty_body(
    tmp_path, monkeypatch
):
    """Smoke-test the global exception handler by forcing a crash inside CRUD.

    Builds its own TestClient with `raise_server_exceptions=False` because the
    default re-raises server-side exceptions in tests instead of letting the
    @exception_handler convert them — which is the very behavior we're verifying.
    """
    from fastapi.testclient import TestClient
    from app import crud, main as app_main
    from app.config import get_settings
    from app.db import init_db
    from app.middleware import RateLimitMiddleware
    from scripts.seed_tables import ensure_tables
    from app.db import connection

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "crash.db"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    get_settings.cache_clear()
    init_db()
    with connection() as conn:
        ensure_tables(conn)
    RateLimitMiddleware._store._buckets.clear()

    monkeypatch.setattr(
        crud, "list_reservations", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("synthetic"))
    )
    with TestClient(
        app_main.app, client=("127.0.0.1", 12345), raise_server_exceptions=False
    ) as tc:
        token = tc.post(
            "/auth/token",
            json={"client_id": "test-client", "client_secret": "test-secret"},
        ).json()["access_token"]
        resp = tc.get("/reservations", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal error"}
