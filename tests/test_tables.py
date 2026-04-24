from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future_iso(days: int = 7, hour: int = 19, minute: int = 0) -> str:
    dt = (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return dt.isoformat()


def _payload(**overrides):
    base = {
        "phone": "+15551234567",
        "customer_name": "Jane Doe",
        "party_size": 4,
        "reservation_at": _future_iso(),
        "notes": None,
    }
    base.update(overrides)
    return base


def test_list_tables_returns_50_with_known_distribution(client, auth_headers):
    resp = client.get("/tables", headers=auth_headers)
    assert resp.status_code == 200
    tables = resp.json()
    assert len(tables) == 50
    by_capacity = {}
    for t in tables:
        by_capacity.setdefault(t["capacity"], []).append(t)
    assert len(by_capacity[2]) == 16
    assert len(by_capacity[4]) == 16
    assert len(by_capacity[6]) == 10
    assert len(by_capacity[8]) == 5
    assert len(by_capacity[10]) == 2
    assert len(by_capacity[12]) == 1


def test_get_table_by_id(client, auth_headers):
    resp = client.get("/tables/1", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["capacity"] == 2


def test_create_reservation_auto_assigns_table(client, auth_headers):
    resp = client.post("/reservations", json=_payload(party_size=4), headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["table_id"] is not None
    assert body["table"] is not None
    assert body["table"]["capacity"] >= 4


def test_auto_assign_picks_smallest_fitting_capacity(client, auth_headers):
    # A party of 2 should land on a capacity-2 table.
    resp = client.post("/reservations", json=_payload(party_size=2), headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["table"]["capacity"] == 2


def test_two_hour_conflict_rejected_same_table(client, auth_headers):
    at = _future_iso(days=10, hour=19, minute=0)
    first = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at),
        headers=auth_headers,
    )
    assert first.status_code == 201
    table_id = first.json()["table_id"]

    # Same table, 1 hour later — should conflict and refuse that specific table.
    one_hour_later = (
        datetime.fromisoformat(at) + timedelta(hours=1)
    ).isoformat()
    resp = client.post(
        "/reservations",
        json=_payload(
            party_size=2,
            reservation_at=one_hour_later,
            table_id=table_id,
        ),
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "2 hours" in resp.json()["detail"]


def test_same_day_same_time_rejected_same_table(client, auth_headers):
    at = _future_iso(days=12, hour=20, minute=0)
    first = client.post(
        "/reservations",
        json=_payload(party_size=4, reservation_at=at),
        headers=auth_headers,
    )
    assert first.status_code == 201
    tid = first.json()["table_id"]

    # Literal same timestamp on the same table.
    resp = client.post(
        "/reservations",
        json=_payload(party_size=4, reservation_at=at, table_id=tid),
        headers=auth_headers,
    )
    assert resp.status_code == 409


def test_exactly_two_hours_apart_is_allowed_same_table(client, auth_headers):
    at = _future_iso(days=14, hour=18, minute=0)
    first = client.post(
        "/reservations",
        json=_payload(party_size=4, reservation_at=at),
        headers=auth_headers,
    )
    assert first.status_code == 201
    tid = first.json()["table_id"]

    # Exactly +2h — boundary is "< 2 hours" conflict, so 2.0h is OK.
    two_h_later = (datetime.fromisoformat(at) + timedelta(hours=2)).isoformat()
    resp = client.post(
        "/reservations",
        json=_payload(party_size=4, reservation_at=two_h_later, table_id=tid),
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["table_id"] == tid


def test_simultaneous_different_tables_ok(client, auth_headers):
    at = _future_iso(days=16, hour=19, minute=30)
    a = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at),
        headers=auth_headers,
    )
    b = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at),
        headers=auth_headers,
    )
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["table_id"] != b.json()["table_id"]


def test_explicit_table_too_small_rejected(client, auth_headers):
    # Table 1 is capacity 2.
    resp = client.post(
        "/reservations",
        json=_payload(party_size=4, table_id=1),
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "too large" in resp.json()["detail"]


def test_unknown_table_id_rejected(client, auth_headers):
    resp = client.post(
        "/reservations",
        json=_payload(party_size=2, table_id=9999),
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert "does not exist" in resp.json()["detail"]


def test_patch_reservation_at_reassigns_on_conflict(client, auth_headers):
    # Create one reservation at T, another at T+5h on the SAME party size.
    at = _future_iso(days=20, hour=18, minute=0)
    r1 = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at),
        headers=auth_headers,
    )
    assert r1.status_code == 201
    five_h = (datetime.fromisoformat(at) + timedelta(hours=5)).isoformat()
    r2 = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=five_h),
        headers=auth_headers,
    )
    assert r2.status_code == 201

    # Patch r2 to within 2h of r1's time — it should get reassigned to a
    # different table rather than failing, because the caller did not pin
    # table_id.
    near = (datetime.fromisoformat(at) + timedelta(hours=1)).isoformat()
    resp = client.patch(
        f"/reservations/{r2.json()['id']}",
        json={"reservation_at": near},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    # Table may change; but the final state must not conflict with r1 on same table.
    if resp.json()["table_id"] == r1.json()["table_id"]:
        raise AssertionError("patched reservation landed on conflicting table")


def test_list_reservations_filter_by_table_id(client, auth_headers):
    # Pin three reservations to table 1 on well-separated days; one on table 2.
    for day in (30, 35, 40):
        resp = client.post(
            "/reservations",
            json=_payload(party_size=2, reservation_at=_future_iso(days=day), table_id=1),
            headers=auth_headers,
        )
        assert resp.status_code == 201
    other = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=_future_iso(days=32), table_id=2),
        headers=auth_headers,
    )
    assert other.status_code == 201

    # Query by table_id via the flexible list endpoint.
    resp = client.get("/reservations", params={"table_id": 1}, headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 3
    assert {r["table_id"] for r in rows} == {1}


def test_nested_tables_reservations_endpoint(client, auth_headers):
    # Put two reservations on table 3; one on table 4.
    for day in (60, 64):
        r = client.post(
            "/reservations",
            json=_payload(party_size=2, reservation_at=_future_iso(days=day), table_id=3),
            headers=auth_headers,
        )
        assert r.status_code == 201
    other = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=_future_iso(days=62), table_id=4),
        headers=auth_headers,
    )
    assert other.status_code == 201

    resp = client.get("/tables/3/reservations", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert {r["table_id"] for r in rows} == {3}


def test_nested_tables_reservations_with_status_filter(client, auth_headers):
    at = _future_iso(days=70)
    created = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at, table_id=5),
        headers=auth_headers,
    )
    assert created.status_code == 201
    client.post(f"/reservations/{created.json()['id']}/cancel", headers=auth_headers)

    confirmed = client.get(
        "/tables/5/reservations",
        params={"status": "confirmed"},
        headers=auth_headers,
    )
    assert confirmed.status_code == 200
    assert len(confirmed.json()) == 0

    cancelled = client.get(
        "/tables/5/reservations",
        params={"status": "cancelled"},
        headers=auth_headers,
    )
    assert cancelled.status_code == 200
    assert len(cancelled.json()) == 1


def test_nested_tables_reservations_404_for_unknown_table(client, auth_headers):
    resp = client.get("/tables/9999/reservations", headers=auth_headers)
    assert resp.status_code == 404


def test_available_returns_all_50_when_empty(client, auth_headers):
    resp = client.get(
        "/tables/available",
        params={"at": _future_iso(days=40, hour=19)},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 50


def test_available_filters_by_capacity(client, auth_headers):
    # party_size=6 should exclude the 32 two-tops and four-tops (16+16),
    # leaving 10+5+2+1 = 18 tables.
    resp = client.get(
        "/tables/available",
        params={"at": _future_iso(days=41, hour=19), "party_size": 6},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    tables = resp.json()
    assert len(tables) == 18
    assert all(t["capacity"] >= 6 for t in tables)
    # Sorted smallest first.
    assert [t["capacity"] for t in tables] == (
        [6] * 10 + [8] * 5 + [10] * 2 + [12]
    )


def test_available_excludes_tables_within_2h_conflict(client, auth_headers):
    at = _future_iso(days=42, hour=19)
    # Book tables 1 and 2 at that exact time.
    for tid in (1, 2):
        r = client.post(
            "/reservations",
            json=_payload(party_size=2, reservation_at=at, table_id=tid),
            headers=auth_headers,
        )
        assert r.status_code == 201

    # Same time, same capacity class → excludes 1 and 2.
    resp = client.get(
        "/tables/available",
        params={"at": at, "party_size": 2},
        headers=auth_headers,
    )
    ids = [t["id"] for t in resp.json()]
    assert 1 not in ids and 2 not in ids
    # 16 cap-2 tables → 14 left. Plus 16+10+5+2+1 = 34 larger. Total 48.
    assert len(ids) == 48


def test_available_outside_2h_window_includes_booked_table(client, auth_headers):
    booked_at = _future_iso(days=43, hour=18)
    r = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=booked_at, table_id=3),
        headers=auth_headers,
    )
    assert r.status_code == 201

    # Check availability exactly 2h later — conflict boundary is strict <2h,
    # so the booked table should reappear.
    two_h_after = (
        datetime.fromisoformat(booked_at) + timedelta(hours=2)
    ).isoformat()
    resp = client.get(
        "/tables/available",
        params={"at": two_h_after, "party_size": 2},
        headers=auth_headers,
    )
    ids = [t["id"] for t in resp.json()]
    assert 3 in ids


def test_available_requires_at_parameter(client, auth_headers):
    resp = client.get("/tables/available", headers=auth_headers)
    assert resp.status_code == 422


def test_cancel_releases_table_for_same_time(client, auth_headers):
    at = _future_iso(days=22, hour=19, minute=0)
    r1 = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at),
        headers=auth_headers,
    )
    assert r1.status_code == 201
    tid = r1.json()["table_id"]

    # Cancel r1; same slot should now be usable again on the same table.
    cancel = client.post(f"/reservations/{r1.json()['id']}/cancel", headers=auth_headers)
    assert cancel.status_code == 200

    r2 = client.post(
        "/reservations",
        json=_payload(party_size=2, reservation_at=at, table_id=tid),
        headers=auth_headers,
    )
    assert r2.status_code == 201
    assert r2.json()["table_id"] == tid
