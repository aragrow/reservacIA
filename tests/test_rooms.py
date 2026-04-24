from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future_iso(days: int = 7, hour: int = 19, minute: int = 0) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    ).isoformat()


def _reservation_payload(**overrides):
    base = {
        "phone": "+15551234567",
        "customer_name": "Jane Doe",
        "party_size": 2,
        "reservation_at": _future_iso(),
        "notes": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- rooms CRUD

def test_create_and_list_rooms(client, auth_headers):
    r = client.post(
        "/rooms",
        json={"name": "Patio", "description": "Outdoor seating"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] > 0
    assert body["name"] == "Patio"

    listed = client.get("/rooms", headers=auth_headers).json()
    assert any(rm["name"] == "Patio" for rm in listed)


def test_room_name_must_be_unique(client, auth_headers):
    client.post("/rooms", json={"name": "Terrace"}, headers=auth_headers)
    dup = client.post("/rooms", json={"name": "Terrace"}, headers=auth_headers)
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]


def test_update_room(client, auth_headers):
    created = client.post(
        "/rooms",
        json={"name": "Lounge"},
        headers=auth_headers,
    ).json()
    resp = client.patch(
        f"/rooms/{created['id']}",
        json={"description": "Live music Fridays"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["description"] == "Live music Fridays"


def test_delete_empty_room_succeeds(client, auth_headers):
    created = client.post(
        "/rooms",
        json={"name": "Private Dining"},
        headers=auth_headers,
    ).json()
    resp = client.delete(f"/rooms/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204
    gone = client.get(f"/rooms/{created['id']}", headers=auth_headers)
    assert gone.status_code == 404


def test_delete_room_with_tables_rejected(client, auth_headers):
    room = client.post("/rooms", json={"name": "Courtyard"}, headers=auth_headers).json()
    client.post(
        "/tables",
        json={"table_number": "C01", "capacity": 4, "room_id": room["id"]},
        headers=auth_headers,
    )
    resp = client.delete(f"/rooms/{room['id']}", headers=auth_headers)
    assert resp.status_code == 409
    assert "still has" in resp.json()["detail"]


def test_get_unknown_room_404(client, auth_headers):
    assert client.get("/rooms/99999", headers=auth_headers).status_code == 404
    assert client.patch("/rooms/99999", json={"name": "X"}, headers=auth_headers).status_code == 404
    assert client.delete("/rooms/99999", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------- tables CRUD

def test_create_table_with_unknown_room_rejected(client, auth_headers):
    resp = client.post(
        "/tables",
        json={"table_number": "X99", "capacity": 4, "room_id": 9999},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "does not exist" in resp.json()["detail"]


def test_create_table_duplicate_number_rejected(client, auth_headers):
    client.post(
        "/tables",
        json={"table_number": "Z01", "capacity": 4},
        headers=auth_headers,
    )
    dup = client.post(
        "/tables",
        json={"table_number": "Z01", "capacity": 6},
        headers=auth_headers,
    )
    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]


def test_update_table_capacity_and_room(client, auth_headers):
    created = client.post(
        "/tables",
        json={"table_number": "U01", "capacity": 4},
        headers=auth_headers,
    ).json()
    room = client.post("/rooms", json={"name": "Mezzanine"}, headers=auth_headers).json()

    resp = client.patch(
        f"/tables/{created['id']}",
        json={"capacity": 8, "room_id": room["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["capacity"] == 8
    assert out["room_id"] == room["id"]
    assert out["room"]["name"] == "Mezzanine"


def test_shrinking_capacity_blocked_when_oversized_confirmed(client, auth_headers):
    created = client.post(
        "/tables",
        json={"table_number": "S01", "capacity": 8},
        headers=auth_headers,
    ).json()
    # Place a confirmed party of 6 on this table.
    client.post(
        "/reservations",
        json=_reservation_payload(party_size=6, table_id=created["id"]),
        headers=auth_headers,
    )
    resp = client.patch(
        f"/tables/{created['id']}",
        json={"capacity": 4},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "confirmed reservation" in resp.json()["detail"]


def test_delete_table_without_references_succeeds(client, auth_headers):
    created = client.post(
        "/tables",
        json={"table_number": "D01", "capacity": 4},
        headers=auth_headers,
    ).json()
    resp = client.delete(f"/tables/{created['id']}", headers=auth_headers)
    assert resp.status_code == 204


def test_delete_table_with_reservation_rejected(client, auth_headers):
    created = client.post(
        "/tables",
        json={"table_number": "D02", "capacity": 4},
        headers=auth_headers,
    ).json()
    client.post(
        "/reservations",
        json=_reservation_payload(party_size=2, table_id=created["id"]),
        headers=auth_headers,
    )
    resp = client.delete(f"/tables/{created['id']}", headers=auth_headers)
    assert resp.status_code == 409
    assert "reassign or cancel" in resp.json()["detail"]


# ---------------------------------------------------------------- nested + filters

def test_list_tables_in_room(client, auth_headers):
    room = client.post("/rooms", json={"name": "Wine Cellar"}, headers=auth_headers).json()
    client.post(
        "/tables",
        json={"table_number": "W01", "capacity": 4, "room_id": room["id"]},
        headers=auth_headers,
    )
    client.post(
        "/tables",
        json={"table_number": "W02", "capacity": 6, "room_id": room["id"]},
        headers=auth_headers,
    )

    via_nested = client.get(f"/rooms/{room['id']}/tables", headers=auth_headers).json()
    assert len(via_nested) == 2
    assert all(t["room_id"] == room["id"] for t in via_nested)

    via_filter = client.get("/tables", params={"room_id": room["id"]}, headers=auth_headers).json()
    assert len(via_filter) == 2


def test_available_tables_filter_by_room(client, auth_headers):
    room = client.post("/rooms", json={"name": "Rooftop"}, headers=auth_headers).json()
    client.post(
        "/tables",
        json={"table_number": "R01", "capacity": 4, "room_id": room["id"]},
        headers=auth_headers,
    )
    client.post(
        "/tables",
        json={"table_number": "R02", "capacity": 6, "room_id": room["id"]},
        headers=auth_headers,
    )

    at = _future_iso(days=30)
    resp = client.get(
        "/tables/available",
        params={"at": at, "room_id": room["id"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    assert all(t["room_id"] == room["id"] for t in resp.json())


def test_nested_room_tables_404(client, auth_headers):
    resp = client.get("/rooms/99999/tables", headers=auth_headers)
    assert resp.status_code == 404
