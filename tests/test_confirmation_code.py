from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _future_iso(days: int = 7) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).isoformat()


def _payload(**overrides):
    base = {
        "phone": "+15551234567",
        "customer_name": "Code Test",
        "party_size": 2,
        "reservation_at": _future_iso(),
        "notes": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------- shape

def test_post_response_includes_confirmation_code(client, auth_headers):
    resp = client.post("/reservations", json=_payload(), headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    code = body["confirmation_code"]
    assert isinstance(code, str)
    assert len(code) == 6
    # Drawn from the 27-char readable alphabet — no confusable glyphs.
    from app.codes import ALPHABET
    assert all(ch in ALPHABET for ch in code)


def test_get_by_id_includes_confirmation_code(client, auth_headers):
    rid = client.post("/reservations", json=_payload(), headers=auth_headers).json()["id"]
    resp = client.get(f"/reservations/{rid}", headers=auth_headers)
    assert resp.status_code == 200
    assert "confirmation_code" in resp.json()


def test_codes_are_unique_across_creates(client, auth_headers):
    seen = set()
    for i in range(10):
        body = _payload(
            phone=f"+1555{i:07d}",
            reservation_at=_future_iso(days=20 + i),
        )
        code = client.post("/reservations", json=body, headers=auth_headers).json()["confirmation_code"]
        assert code not in seen
        seen.add(code)


# ---------------------------------------------------------- lookup

def test_lookup_by_code_canonical_form(client, auth_headers):
    code = client.post("/reservations", json=_payload(), headers=auth_headers).json()["confirmation_code"]
    resp = client.get(f"/reservations/by-code/{code}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["confirmation_code"] == code


def test_lookup_by_code_is_case_insensitive(client, auth_headers):
    code = client.post("/reservations", json=_payload(), headers=auth_headers).json()["confirmation_code"]
    resp = client.get(f"/reservations/by-code/{code.lower()}", headers=auth_headers)
    assert resp.status_code == 200


def test_lookup_by_code_accepts_dash(client, auth_headers):
    code = client.post("/reservations", json=_payload(), headers=auth_headers).json()["confirmation_code"]
    dashed = f"{code[:3]}-{code[3:]}"
    resp = client.get(f"/reservations/by-code/{dashed}", headers=auth_headers)
    assert resp.status_code == 200


def test_lookup_unknown_code_returns_404(client, auth_headers):
    resp = client.get("/reservations/by-code/ZZZZZZ", headers=auth_headers)
    assert resp.status_code == 404


def test_lookup_finds_cancelled_reservations(client, auth_headers):
    """Cancelled rows should still be findable by code (history reference)."""
    created = client.post("/reservations", json=_payload(), headers=auth_headers).json()
    code = created["confirmation_code"]
    client.post(f"/reservations/{created['id']}/cancel", headers=auth_headers)

    resp = client.get(f"/reservations/by-code/{code}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------- immutability

def test_patch_does_not_change_confirmation_code(client, auth_headers):
    created = client.post("/reservations", json=_payload(), headers=auth_headers).json()
    rid, code = created["id"], created["confirmation_code"]

    patched = client.patch(
        f"/reservations/{rid}",
        json={"party_size": 4, "notes": "updated"},
        headers=auth_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["confirmation_code"] == code


def test_patch_rejects_attempt_to_set_confirmation_code(client, auth_headers):
    """ReservationUpdate has extra='forbid'; clients cannot supply or change it."""
    created = client.post("/reservations", json=_payload(), headers=auth_headers).json()
    resp = client.patch(
        f"/reservations/{created['id']}",
        json={"confirmation_code": "HACKED"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------- backfill

def test_backfill_assigns_codes_to_pre_existing_rows(client, auth_headers):
    """Simulate a row that pre-existed the migration — directly null the code,
    re-run init_db, assert the row is rehydrated with a fresh code."""
    from app.db import connection, init_db

    rid = client.post("/reservations", json=_payload(), headers=auth_headers).json()["id"]

    with connection() as conn:
        conn.execute("UPDATE reservations SET confirmation_code = NULL WHERE id = ?", (rid,))

    init_db()  # idempotent; the lazy backfill in _migrate populates the NULL

    resp = client.get(f"/reservations/{rid}", headers=auth_headers)
    assert resp.status_code == 200
    new_code = resp.json()["confirmation_code"]
    assert isinstance(new_code, str) and len(new_code) == 6


# ---------------------------------------------------------- alphabet sanity

def test_alphabet_excludes_visually_confusable_glyphs():
    from app.codes import ALPHABET
    for bad in "BILOUV018":
        assert bad not in ALPHABET, f"alphabet must not include confusable {bad!r}"
