"""SQL helpers for the notifications queue.

All callers go through these helpers — never raw SQL on the table — so the
status state machine (`pending → in_flight → sent | failed | cancelled`) is
enforced in one place.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings

_COLUMNS = (
    "id, reservation_id, kind, phone, scheduled_at, status, attempts, "
    "last_error, body, sent_at, created_at, updated_at"
)


def _now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def enqueue(
    conn: sqlite3.Connection,
    *,
    reservation_id: int | None,
    kind: str,
    phone: str,
    scheduled_at: datetime,
    body: str,
) -> int:
    """Insert a pending notification row. Returns the new id.

    `reservation_id` is optional — agent-driven `custom` messages can be
    untethered from any booking. `scheduled_at` is normalised to UTC ISO
    before storage so that the `pick_due` comparison (a lexical string
    compare in SQLite) sorts correctly across rows that came in with
    different offsets.
    """
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=ZoneInfo(get_settings().timezone))
    scheduled_at_utc = scheduled_at.astimezone(timezone.utc)
    cur = conn.execute(
        """
        INSERT INTO notifications
            (reservation_id, kind, phone, scheduled_at, body, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
        """,
        (reservation_id, kind, phone, scheduled_at_utc.isoformat(), body),
    )
    return cur.lastrowid  # type: ignore[return-value]


def cancel_pending_reminders_for(
    conn: sqlite3.Connection, reservation_id: int
) -> int:
    """Mark any still-pending reminder for this reservation as 'cancelled'.

    Used when the reservation's time changes (the old reminder is no longer
    accurate) and when the reservation itself is cancelled.
    Returns the number of rows updated.
    """
    cur = conn.execute(
        """
        UPDATE notifications
           SET status = 'cancelled', updated_at = datetime('now')
         WHERE reservation_id = ?
           AND kind = 'reminder'
           AND status = 'pending'
        """,
        (reservation_id,),
    )
    return cur.rowcount


def pick_due(
    conn: sqlite3.Connection, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Atomically claim up to `limit` pending rows whose scheduled_at <= now,
    flipping them to 'in_flight'. Returns the claimed rows.

    Uses an UPDATE-with-subselect pattern instead of UPDATE...RETURNING to keep
    compatibility with older SQLite builds. The two-step approach is fine here
    because there's only one worker process.
    """
    now_iso = _now_utc_iso()
    rows = conn.execute(
        """
        SELECT id FROM notifications
         WHERE status = 'pending' AND scheduled_at <= ?
         ORDER BY scheduled_at ASC, id ASC
         LIMIT ?
        """,
        (now_iso, limit),
    ).fetchall()
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE notifications SET status = 'in_flight', "
        f"updated_at = datetime('now'), attempts = attempts + 1 "
        f"WHERE id IN ({placeholders})",
        ids,
    )
    return [
        dict(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM notifications WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    ]


def mark_sent(conn: sqlite3.Connection, notification_id: int) -> None:
    conn.execute(
        """
        UPDATE notifications
           SET status = 'sent', sent_at = datetime('now'),
               updated_at = datetime('now'), last_error = NULL
         WHERE id = ?
        """,
        (notification_id,),
    )


def mark_failed_or_retry(
    conn: sqlite3.Connection,
    notification_id: int,
    error: str,
    *,
    max_attempts: int,
) -> None:
    """Either reset to 'pending' for retry (with a back-off delay applied to
    `scheduled_at`), or mark 'failed' if attempts exceeded the cap."""
    row = conn.execute(
        "SELECT attempts FROM notifications WHERE id = ?", (notification_id,)
    ).fetchone()
    if row is None:
        return
    attempts = row["attempts"]
    if attempts >= max_attempts:
        conn.execute(
            """
            UPDATE notifications
               SET status = 'failed', last_error = ?, updated_at = datetime('now')
             WHERE id = ?
            """,
            (error[:1000], notification_id),
        )
        return
    backoff = min(60 * (2 ** attempts), 3600)  # cap at 1h
    next_at = (
        datetime.now(tz=timezone.utc) + timedelta(seconds=backoff)
    ).isoformat()
    conn.execute(
        """
        UPDATE notifications
           SET status = 'pending', last_error = ?, scheduled_at = ?,
               updated_at = datetime('now')
         WHERE id = ?
        """,
        (error[:1000], next_at, notification_id),
    )


def list_for_reservation(
    conn: sqlite3.Connection, reservation_id: int
) -> list[dict[str, Any]]:
    """Test/diagnostics helper — return all queue rows for one reservation."""
    return [
        dict(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM notifications "
            "WHERE reservation_id = ? ORDER BY id ASC",
            (reservation_id,),
        ).fetchall()
    ]


def list_notifications(
    conn: sqlite3.Connection,
    *,
    phone: str | None = None,
    reservation_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Filtered listing for the admin/agent endpoint. Newest first by id."""
    clauses: list[str] = []
    params: list[Any] = []
    if phone is not None:
        clauses.append("phone = ?")
        params.append(phone)
    if reservation_id is not None:
        clauses.append("reservation_id = ?")
        params.append(reservation_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM notifications {where} "
        "ORDER BY id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_notification(
    conn: sqlite3.Connection, notification_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM notifications WHERE id = ?",
        (notification_id,),
    ).fetchone()
    return dict(row) if row else None
