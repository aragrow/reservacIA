from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ----------------------------------------------------------- helpers

def _future_iso(days: int = 7, hour: int = 19) -> str:
    return (datetime.now(tz=timezone.utc) + timedelta(days=days)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat()


def _payload(**overrides):
    base = {
        "phone": "+34911234567",
        "customer_name": "Andrés Notif",
        "party_size": 2,
        "reservation_at": _future_iso(),
        "notes": None,
    }
    base.update(overrides)
    return base


def _list_queue(reservation_id: int) -> list[dict]:
    from app.db import connection
    from app.notifications import queue
    with connection() as conn:
        return queue.list_for_reservation(conn, reservation_id)


# ----------------------------------------------------------- templates

def test_template_render_uses_locale_format():
    from app.notifications.templates import render
    body = render(
        "created",
        {
            "id": 1,
            "phone": "+34900111222",
            "party_size": 2,
            "reservation_at": "2026-05-25T18:00:00+02:00",
            "confirmation_code": "BUR7K3",
            "table": {"room": {"name": "Salón La Posada"}},
        },
    )
    assert "25/05/2026" in body         # dd/mm/yyyy
    assert "18:00" in body              # 24-hour
    assert "BUR-7K3" in body            # human-friendly dash inserted
    assert "Salón La Posada" in body
    assert "La Posada de la Pepa" in body


def test_template_handles_missing_room():
    """Of the four templates, `created` and `updated` reference {room};
    the fallback string must appear when the reservation has no table/room."""
    from app.notifications.templates import render
    body = render(
        "created",
        {
            "id": 1, "phone": "+34900111222", "party_size": 4,
            "reservation_at": "2026-05-25T18:00:00+02:00",
            "confirmation_code": "ABC234", "table": None,
        },
    )
    assert "el restaurante" in body


def test_template_unknown_kind_raises():
    from app.notifications.templates import render
    import pytest
    with pytest.raises(ValueError):
        render("nope", {"id": 1})


# ----------------------------------------------------------- CRUD hook lifecycle

def test_create_enqueues_created_and_reminder(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    rows = _list_queue(rid)
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["created", "reminder"]
    assert all(r["status"] == "pending" for r in rows)
    # Reminder is in the future relative to the created notification.
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["reminder"]["scheduled_at"] > by_kind["created"]["scheduled_at"]


def test_create_late_reminder_is_immediate(client, auth_headers):
    """If reservation_at is < lead-hours away, the reminder fires now."""
    soon = _future_iso(days=0, hour=(datetime.now(tz=timezone.utc).hour + 2) % 24)
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=soon),
        headers=auth_headers,
    ).json()["id"]
    rows = _list_queue(rid)
    by_kind = {r["kind"]: r for r in rows}
    # Created and reminder scheduled within seconds of each other.
    diff = abs(
        datetime.fromisoformat(by_kind["reminder"]["scheduled_at"])
        - datetime.fromisoformat(by_kind["created"]["scheduled_at"])
    )
    assert diff.total_seconds() < 5


def test_patch_time_cancels_old_reminder_and_enqueues_updated_plus_new_reminder(
    client, auth_headers
):
    created = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()
    rid = created["id"]

    client.patch(
        f"/reservations/{rid}",
        json={"reservation_at": _future_iso(days=10)},
        headers=auth_headers,
    )

    rows = _list_queue(rid)
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    # Two reminders ever existed; the first is cancelled, the second is pending.
    assert len(by_kind["reminder"]) == 2
    statuses = sorted(r["status"] for r in by_kind["reminder"])
    assert statuses == ["cancelled", "pending"]
    # An 'updated' message was enqueued.
    assert len(by_kind["updated"]) == 1


def test_patch_party_size_only_enqueues_updated_no_reminder_change(
    client, auth_headers
):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    client.patch(
        f"/reservations/{rid}",
        json={"party_size": 4},
        headers=auth_headers,
    )
    rows = _list_queue(rid)
    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    assert len(by_kind["updated"]) == 1
    # The original reminder is untouched.
    assert len(by_kind["reminder"]) == 1
    assert by_kind["reminder"][0]["status"] == "pending"


def test_patch_notes_only_is_silent(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    before = len(_list_queue(rid))
    client.patch(
        f"/reservations/{rid}",
        json={"notes": "tarta de cumpleaños"},
        headers=auth_headers,
    )
    after = _list_queue(rid)
    assert len(after) == before  # no new rows for note-only edits


def test_cancel_enqueues_cancelled_and_drops_pending_reminder(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    client.post(f"/reservations/{rid}/cancel", headers=auth_headers)

    rows = _list_queue(rid)
    by_kind = {r["kind"]: [x for x in rows if x["kind"] == r["kind"]] for r in rows}
    assert any(r["kind"] == "cancelled" and r["status"] == "pending" for r in rows)
    # Original reminder is now in the cancelled state (the queue's internal status).
    reminder = next(r for r in rows if r["kind"] == "reminder")
    assert reminder["status"] == "cancelled"


# ----------------------------------------------------------- queue helpers

def test_pick_due_atomically_flips_to_in_flight(client, auth_headers):
    """A second call after pick_due returns no rows again — they're in_flight."""
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    from app.db import connection
    from app.notifications import queue

    # The 'created' row is scheduled at "now" so it's due immediately.
    with connection() as conn:
        first = queue.pick_due(conn, limit=10)
    with connection() as conn:
        second = queue.pick_due(conn, limit=10)

    assert any(r["reservation_id"] == rid and r["kind"] == "created" for r in first)
    # A second pick must not return the same rows — they're 'in_flight' now.
    first_ids = {r["id"] for r in first}
    second_ids = {r["id"] for r in second}
    assert first_ids.isdisjoint(second_ids)


def test_mark_failed_or_retry_eventually_marks_failed(client, auth_headers):
    """After max_attempts hits, the row transitions to 'failed' instead of pending."""
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    from app.db import connection
    from app.notifications import queue

    # Bump attempts past the cap, then call mark_failed_or_retry.
    with connection() as conn:
        row_id = queue.pick_due(conn, limit=1)[0]["id"]
        conn.execute(
            "UPDATE notifications SET attempts = ? WHERE id = ?",
            (5, row_id),
        )
        queue.mark_failed_or_retry(conn, row_id, "synthetic", max_attempts=5)
        status = conn.execute(
            "SELECT status FROM notifications WHERE id = ?", (row_id,)
        ).fetchone()["status"]
    assert status == "failed"


# ----------------------------------------------------------- worker

def test_worker_dispatches_pending_to_notifier(client, auth_headers):
    """End-to-end through `process_batch`: a captured-list MockNotifier sees
    each due row exactly once, and rows transition to 'sent'."""
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    sent: list[dict] = []

    class MockNotifier:
        def send(self, *, phone: str, body: str) -> None:
            sent.append({"phone": phone, "body": body})

    from app.config import get_settings
    from app.notifications.worker import process_batch
    process_batch(MockNotifier(), get_settings())

    rows = _list_queue(rid)
    sent_kinds = sorted(r["kind"] for r in rows if r["status"] == "sent")
    # Only 'created' was due (reminder is days away). It went to the notifier.
    assert sent_kinds == ["created"]
    assert any(s["body"].startswith("¡Reserva confirmada!") for s in sent)


# ----------------------------------------------------------- HTTP endpoint

def test_list_endpoint_returns_recent_rows(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    resp = client.get("/notifications", headers=auth_headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 2
    assert all(r["reservation_id"] == rid for r in rows[:2])
    # Newest first.
    assert rows[0]["id"] > rows[-1]["id"]


def test_list_endpoint_filters_by_phone(client, auth_headers):
    rid_a = client.post(
        "/reservations",
        json=_payload(phone="+34611111111", reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    client.post(
        "/reservations",
        json=_payload(phone="+34622222222", reservation_at=_future_iso(days=8)),
        headers=auth_headers,
    )
    # `params=` ensures `+` is properly URL-encoded.
    resp = client.get(
        "/notifications",
        params={"phone": "+34611111111"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2  # created + reminder for the one matching phone
    assert all(r["phone"] == "+34611111111" for r in rows)
    assert all(r["reservation_id"] == rid_a for r in rows)


def test_list_endpoint_filters_by_status_and_kind(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    only_reminder = client.get(
        "/notifications",
        params={"reservation_id": rid, "kind": "reminder"},
        headers=auth_headers,
    ).json()
    assert len(only_reminder) == 1
    assert only_reminder[0]["kind"] == "reminder"

    only_pending = client.get(
        "/notifications",
        params={"reservation_id": rid, "status": "pending"},
        headers=auth_headers,
    ).json()
    assert len(only_pending) == 2  # both rows still pending right after POST


def test_list_endpoint_pagination(client, auth_headers):
    # Create 3 reservations → 6 notification rows.
    for i in range(3):
        client.post(
            "/reservations",
            json=_payload(
                phone=f"+3460000{i:04d}",
                reservation_at=_future_iso(days=7 + i),
            ),
            headers=auth_headers,
        )
    page1 = client.get(
        "/notifications", params={"limit": 2, "offset": 0}, headers=auth_headers
    ).json()
    page2 = client.get(
        "/notifications", params={"limit": 2, "offset": 2}, headers=auth_headers
    ).json()
    assert len(page1) == 2 and len(page2) == 2
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


def test_get_one_endpoint(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    listed = client.get(
        "/notifications",
        params={"reservation_id": rid},
        headers=auth_headers,
    ).json()
    nid = listed[0]["id"]
    resp = client.get(f"/notifications/{nid}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == nid
    assert "body" in resp.json()


def test_get_one_unknown_id_returns_404(client, auth_headers):
    resp = client.get("/notifications/9999", headers=auth_headers)
    assert resp.status_code == 404


def test_list_endpoint_requires_auth(client):
    resp = client.get("/notifications")
    assert resp.status_code == 401


def test_list_endpoint_appears_in_openapi(client, auth_headers):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/notifications" in paths
    assert "/notifications/{notification_id}" in paths
    methods = paths["/notifications"]
    assert "get" in methods and "post" in methods
    methods_byid = paths["/notifications/{notification_id}"]
    assert "get" in methods_byid


# ----------------------------------------------------------- POST endpoint

def test_post_creates_custom_notification_without_reservation(client, auth_headers):
    """An agent can post a free-form message tied to no reservation."""
    resp = client.post(
        "/notifications",
        json={"phone": "+34977000111", "body": "Mensaje del agente — bienvenido."},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["kind"] == "custom"
    assert out["status"] == "pending"
    assert out["reservation_id"] is None
    assert out["body"] == "Mensaje del agente — bienvenido."
    assert out["phone"] == "+34977000111"


def test_post_with_existing_reservation_id_succeeds(client, auth_headers):
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    resp = client.post(
        "/notifications",
        json={
            "phone": "+34977000222",
            "body": "Confirmación del agente.",
            "reservation_id": rid,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["reservation_id"] == rid


def test_post_with_unknown_reservation_id_returns_404(client, auth_headers):
    resp = client.post(
        "/notifications",
        json={
            "phone": "+34977000333", "body": "x", "reservation_id": 999999,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert "does not exist" in resp.json()["detail"]


def test_post_with_future_scheduled_at_stays_pending_until_due(client, auth_headers):
    future = _future_iso(days=2)
    resp = client.post(
        "/notifications",
        json={
            "phone": "+34977000444",
            "body": "Mensaje programado.",
            "scheduled_at": future,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    nid = resp.json()["id"]

    # Worker tick now → should NOT pick it up because scheduled_at is in the future.
    class CapturingNotifier:
        sent: list[str] = []
        def send(self, *, phone: str, body: str) -> None:
            self.sent.append(body)

    from app.config import get_settings
    from app.notifications.worker import process_batch
    notif = CapturingNotifier()
    process_batch(notif, get_settings())

    # Confirm the row is still pending.
    from app.db import connection
    from app.notifications import queue
    with connection() as conn:
        row = queue.get_notification(conn, nid)
    assert row["status"] == "pending"
    assert "Mensaje programado." not in notif.sent


def test_post_then_worker_dispatches(client, auth_headers):
    """End-to-end: agent posts → worker tick → notifier sees it."""
    resp = client.post(
        "/notifications",
        json={"phone": "+34977000555", "body": "Hola desde el agente."},
        headers=auth_headers,
    )
    nid = resp.json()["id"]

    class MockNotifier:
        last: dict | None = None
        def send(self, *, phone: str, body: str) -> None:
            self.last = {"phone": phone, "body": body}

    from app.config import get_settings
    from app.notifications.worker import process_batch
    notifier = MockNotifier()
    process_batch(notifier, get_settings())

    from app.db import connection
    from app.notifications import queue
    with connection() as conn:
        row = queue.get_notification(conn, nid)
    assert row["status"] == "sent"
    assert notifier.last == {"phone": "+34977000555", "body": "Hola desde el agente."}


def test_post_validates_phone_and_body(client, auth_headers):
    # Empty body → 422
    bad = client.post(
        "/notifications", json={"phone": "+34977000666", "body": ""},
        headers=auth_headers,
    )
    assert bad.status_code == 422
    # Bad phone → 422
    bad2 = client.post(
        "/notifications", json={"phone": "not-a-phone", "body": "x"},
        headers=auth_headers,
    )
    assert bad2.status_code == 422


def test_post_requires_auth(client):
    resp = client.post(
        "/notifications", json={"phone": "+34977000777", "body": "x"},
    )
    assert resp.status_code == 401


def test_worker_swallows_send_failures_and_marks_for_retry(client, auth_headers):
    """A flaky provider must not stall the queue: failed rows return to
    'pending' (or 'failed' after enough attempts) and the worker keeps going."""
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]

    class BoomNotifier:
        def send(self, *, phone: str, body: str) -> None:
            raise RuntimeError("simulated provider outage")

    from app.config import get_settings
    from app.notifications.worker import process_batch
    process_batch(BoomNotifier(), get_settings())

    rows = _list_queue(rid)
    created = next(r for r in rows if r["kind"] == "created")
    # First failure leaves the row pending (with backoff) — not 'failed' yet.
    assert created["status"] == "pending"
    assert created["attempts"] >= 1
    assert "simulated provider outage" in (created["last_error"] or "")


def test_suppress_notifications_drops_lifecycle_but_allows_custom(
    monkeypatch, client, auth_headers
):
    """With the kill switch on:
       - lifecycle kinds are not enqueued (queue stays empty for the booking)
       - agent-driven `custom` messages still enqueue and dispatch normally,
         so the agent's outbound activity remains auditable.
    """
    from app.config import get_settings
    monkeypatch.setenv("SUPPRESS_NOTIFICATIONS", "true")
    get_settings.cache_clear()
    try:
        rid = client.post(
            "/reservations",
            json=_payload(reservation_at=_future_iso(days=7)),
            headers=auth_headers,
        ).json()["id"]
        # Lifecycle (created + reminder) suppressed → no rows for this booking.
        assert _list_queue(rid) == []

        # Agent POST succeeds with 201 and a tracked row.
        resp = client.post(
            "/notifications",
            json={"phone": "+34977000777", "body": "Recordatorio del agente."},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        nid = resp.json()["id"]

        # Worker still dispatches custom rows even while suppressed.
        class MockNotifier:
            last: dict | None = None
            def send(self, *, phone: str, body: str) -> None:
                self.last = {"phone": phone, "body": body}

        from app.notifications.worker import process_batch
        notifier = MockNotifier()
        sent = process_batch(notifier, get_settings())

        assert sent == 1
        assert notifier.last == {
            "phone": "+34977000777", "body": "Recordatorio del agente."
        }
        from app.db import connection
        from app.notifications import queue
        with connection() as conn:
            row = queue.get_notification(conn, nid)
        assert row["status"] == "sent"
    finally:
        get_settings.cache_clear()


def test_suppress_notifications_worker_skips_preexisting_lifecycle_rows(
    monkeypatch, client, auth_headers
):
    """If lifecycle rows were already in the queue before the kill switch
    flipped on, the worker must not dispatch them — only `custom` rows go out."""
    # Create reservation while suppression is off → lifecycle rows enqueue.
    rid = client.post(
        "/reservations",
        json=_payload(reservation_at=_future_iso(days=7)),
        headers=auth_headers,
    ).json()["id"]
    pre = _list_queue(rid)
    assert any(r["kind"] == "created" for r in pre)

    # Now flip the kill switch on.
    from app.config import get_settings
    monkeypatch.setenv("SUPPRESS_NOTIFICATIONS", "true")
    get_settings.cache_clear()
    try:
        class TattleNotifier:
            calls = 0
            def send(self, *, phone: str, body: str) -> None:
                self.calls += 1

        from app.notifications.worker import process_batch
        notifier = TattleNotifier()
        sent = process_batch(notifier, get_settings())

        assert sent == 0
        assert notifier.calls == 0
        created = next(r for r in _list_queue(rid) if r["kind"] == "created")
        assert created["status"] == "pending"
        assert created["attempts"] == 0
    finally:
        get_settings.cache_clear()
