from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future_iso(days: int = 7) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()


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
