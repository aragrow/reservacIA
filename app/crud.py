from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import (
    ReservationCreate,
    ReservationUpdate,
    ReviewCommentCreate,
    ReviewCommentUpdate,
    ReviewCreate,
    ReviewUpdate,
    RoomCreate,
    RoomUpdate,
    TableCreate,
    TableUpdate,
)

CONFLICT_WINDOW = timedelta(hours=2)  # no two reservations on same table within 2h

_RESERVATION_COLUMNS = (
    "id, phone, customer_name, party_size, reservation_at, notes, status, "
    "table_id, confirmation_code, created_at, updated_at"
)
_TABLE_COLUMNS = "id, table_number, capacity, room_id, created_at"
_ROOM_COLUMNS = "id, name, description, created_at, updated_at"
_REVIEW_COLUMNS = (
    "id, reviewer_name, reviewer_city, rating, body, created_at, updated_at"
)
_REVIEW_COMMENT_COLUMNS = (
    "id, review_id, author_role, author_name, body, created_at, updated_at"
)


class ReservationError(Exception):
    """Raised for domain-level validation failures (mapped to 409 Conflict)."""


class DomainError(Exception):
    """Raised for room/table domain-level failures (mapped to 409 Conflict)."""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, always returning a timezone-aware datetime.

    - Trailing 'Z' is mapped to '+00:00' (fromisoformat handles 'Z' only on 3.11+,
      and quoting it explicitly is unambiguous).
    - Naive strings (no offset suffix) are interpreted as the restaurant's local
      timezone — same convention as the Pydantic validator at the API boundary.
      This keeps every datetime that flows through conflict-detection aware,
      so we never crash on `aware - naive` comparisons.
    """
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(get_settings().timezone))
    return dt


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# --- rooms -------------------------------------------------------------------

def list_rooms(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_ROOM_COLUMNS} FROM rooms ORDER BY id ASC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_room(conn: sqlite3.Connection, room_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_ROOM_COLUMNS} FROM rooms WHERE id = ?", (room_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def create_room(conn: sqlite3.Connection, data: RoomCreate) -> dict[str, Any]:
    try:
        cur = conn.execute(
            "INSERT INTO rooms (name, description) VALUES (?, ?)",
            (data.name, data.description),
        )
    except sqlite3.IntegrityError as exc:
        raise DomainError(f"room name '{data.name}' already exists") from exc
    return get_room(conn, cur.lastrowid)  # type: ignore[arg-type, return-value]


def update_room(
    conn: sqlite3.Connection, room_id: int, data: RoomUpdate
) -> Optional[dict[str, Any]]:
    existing = get_room(conn, room_id)
    if existing is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return existing
    sets = [f"{k} = ?" for k in changes] + ["updated_at = datetime('now')"]
    params = list(changes.values()) + [room_id]
    try:
        conn.execute(f"UPDATE rooms SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.IntegrityError as exc:
        raise DomainError(f"room name already exists") from exc
    return get_room(conn, room_id)


def delete_room(conn: sqlite3.Connection, room_id: int) -> bool:
    existing = get_room(conn, room_id)
    if existing is None:
        return False
    in_use = conn.execute(
        "SELECT COUNT(*) FROM tables WHERE room_id = ?", (room_id,)
    ).fetchone()[0]
    if in_use > 0:
        raise DomainError(
            f"room '{existing['name']}' still has {in_use} table(s) assigned; "
            f"move or delete them first"
        )
    conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    return True


# --- tables ------------------------------------------------------------------

def _attach_room(conn: sqlite3.Connection, table_row: dict[str, Any]) -> dict[str, Any]:
    table_row["room"] = get_room(conn, table_row["room_id"]) if table_row.get("room_id") else None
    return table_row


def list_tables(
    conn: sqlite3.Connection,
    room_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    if room_id is not None:
        rows = conn.execute(
            f"SELECT {_TABLE_COLUMNS} FROM tables WHERE room_id = ? "
            "ORDER BY capacity ASC, id ASC",
            (room_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {_TABLE_COLUMNS} FROM tables ORDER BY capacity ASC, id ASC"
        ).fetchall()
    return [_attach_room(conn, _row_to_dict(r)) for r in rows]


def get_table(conn: sqlite3.Connection, table_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_TABLE_COLUMNS} FROM tables WHERE id = ?", (table_id,)
    ).fetchone()
    return _attach_room(conn, _row_to_dict(row)) if row else None


def create_table(conn: sqlite3.Connection, data: TableCreate) -> dict[str, Any]:
    if data.room_id is not None and get_room(conn, data.room_id) is None:
        raise DomainError(f"room {data.room_id} does not exist")
    try:
        cur = conn.execute(
            "INSERT INTO tables (table_number, capacity, room_id) VALUES (?, ?, ?)",
            (data.table_number, data.capacity, data.room_id),
        )
    except sqlite3.IntegrityError as exc:
        raise DomainError(f"table_number '{data.table_number}' already exists") from exc
    return get_table(conn, cur.lastrowid)  # type: ignore[arg-type, return-value]


def update_table(
    conn: sqlite3.Connection, table_id: int, data: TableUpdate
) -> Optional[dict[str, Any]]:
    existing = get_table(conn, table_id)
    if existing is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return existing

    # Cross-field validation: shrinking capacity must not strand existing parties.
    if "capacity" in changes and changes["capacity"] < existing["capacity"]:
        oversized = conn.execute(
            "SELECT COUNT(*) FROM reservations "
            "WHERE table_id = ? AND status = 'confirmed' AND party_size > ?",
            (table_id, changes["capacity"]),
        ).fetchone()[0]
        if oversized > 0:
            raise DomainError(
                f"cannot shrink capacity to {changes['capacity']}: "
                f"{oversized} confirmed reservation(s) have larger parties"
            )
    if "room_id" in changes and changes["room_id"] is not None:
        if get_room(conn, changes["room_id"]) is None:
            raise DomainError(f"room {changes['room_id']} does not exist")

    sets = [f"{k} = ?" for k in changes]
    params = list(changes.values()) + [table_id]
    try:
        conn.execute(f"UPDATE tables SET {', '.join(sets)} WHERE id = ?", params)
    except sqlite3.IntegrityError as exc:
        raise DomainError(f"table_number already exists") from exc
    return get_table(conn, table_id)


def delete_table(conn: sqlite3.Connection, table_id: int) -> bool:
    existing = get_table(conn, table_id)
    if existing is None:
        return False
    in_use = conn.execute(
        "SELECT COUNT(*) FROM reservations WHERE table_id = ?", (table_id,)
    ).fetchone()[0]
    if in_use > 0:
        raise DomainError(
            f"table {existing['table_number']} is referenced by {in_use} reservation(s); "
            f"reassign or cancel them first"
        )
    conn.execute("DELETE FROM tables WHERE id = ?", (table_id,))
    return True


# --- conflict detection + availability ---------------------------------------

def table_has_conflict(
    conn: sqlite3.Connection,
    table_id: int,
    at: datetime,
    exclude_reservation_id: Optional[int] = None,
) -> bool:
    """True if `table_id` has a confirmed reservation within 2h of `at`."""
    rows = conn.execute(
        """
        SELECT id, reservation_at FROM reservations
         WHERE table_id = ? AND status = 'confirmed'
        """,
        (table_id,),
    ).fetchall()
    for row in rows:
        if exclude_reservation_id is not None and row["id"] == exclude_reservation_id:
            continue
        delta = abs(_parse_ts(row["reservation_at"]) - at)
        if delta < CONFLICT_WINDOW:
            return True
    return False


def find_all_available_tables(
    conn: sqlite3.Connection,
    at: datetime,
    party_size: Optional[int] = None,
    room_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Tables with no confirmed reservation within 2h of `at`.

    Optional filters: `party_size` (capacity >=), `room_id` (only that room).
    Ordered smallest-capacity-first so the first element is the best fit.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if party_size is not None:
        clauses.append("capacity >= ?")
        params.append(party_size)
    if room_id is not None:
        clauses.append("room_id = ?")
        params.append(room_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT {_TABLE_COLUMNS} FROM tables {where} "
        "ORDER BY capacity ASC, id ASC",
        params,
    ).fetchall()
    return [
        _attach_room(conn, _row_to_dict(r)) for r in rows
        if not table_has_conflict(conn, r["id"], at)
    ]


def find_available_table(
    conn: sqlite3.Connection,
    party_size: int,
    at: datetime,
    exclude_reservation_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Return the smallest-capacity table that fits and has no 2h conflict.

    Ties (same capacity) are broken by current load so assignments spread across
    the floor instead of piling onto the lowest id.
    """
    candidates = conn.execute(
        f"""
        SELECT {_TABLE_COLUMNS}, (
            SELECT COUNT(*) FROM reservations r
             WHERE r.table_id = tables.id AND r.status = 'confirmed'
        ) AS load
        FROM tables
        WHERE capacity >= ?
        ORDER BY capacity ASC, load ASC, id ASC
        """,
        (party_size,),
    ).fetchall()
    for row in candidates:
        if not table_has_conflict(conn, row["id"], at, exclude_reservation_id):
            return _attach_room(conn, {k: row[k] for k in ("id", "table_number", "capacity", "room_id", "created_at")})
    return None


def _resolve_table_for(
    conn: sqlite3.Connection,
    *,
    requested_table_id: Optional[int],
    party_size: int,
    at: datetime,
    exclude_reservation_id: Optional[int] = None,
) -> int:
    """Pick a table id honoring capacity + 2h rule, or raise ReservationError."""
    if requested_table_id is not None:
        table = get_table(conn, requested_table_id)
        if table is None:
            raise ReservationError(f"table {requested_table_id} does not exist")
        if table["capacity"] < party_size:
            raise ReservationError(
                f"table {table['table_number']} seats {table['capacity']}, "
                f"party of {party_size} too large"
            )
        if table_has_conflict(conn, requested_table_id, at, exclude_reservation_id):
            raise ReservationError(
                f"table {table['table_number']} has another reservation within 2 hours"
            )
        return requested_table_id

    chosen = find_available_table(conn, party_size, at, exclude_reservation_id)
    if chosen is None:
        raise ReservationError(
            f"no table available for party of {party_size} at {at.isoformat()}"
        )
    return chosen["id"]


# --- reservations ------------------------------------------------------------

def _attach_table(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    row["table"] = get_table(conn, row["table_id"]) if row.get("table_id") else None
    return row


def _generate_unique_code(conn: sqlite3.Connection) -> str:
    """Pick a confirmation code that isn't already taken. Retries on collision —
    the 27**6 ≈ 387 M key space makes that path effectively never run, but the
    loop is cheap belt-and-suspenders."""
    from app.codes import generate_code
    for _ in range(8):
        code = generate_code()
        existing = conn.execute(
            "SELECT 1 FROM reservations WHERE confirmation_code = ?", (code,)
        ).fetchone()
        if existing is None:
            return code
    raise RuntimeError("could not generate a unique confirmation code")


def create_reservation(
    conn: sqlite3.Connection, data: ReservationCreate
) -> dict[str, Any]:
    at = data.reservation_at
    table_id = _resolve_table_for(
        conn,
        requested_table_id=data.table_id,
        party_size=data.party_size,
        at=at,
    )
    code = _generate_unique_code(conn)
    cur = conn.execute(
        """
        INSERT INTO reservations
            (phone, customer_name, party_size, reservation_at, notes,
             table_id, confirmation_code)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.phone, data.customer_name, data.party_size, _iso(at),
            data.notes, table_id, code,
        ),
    )
    return get_reservation(conn, cur.lastrowid)  # type: ignore[return-value, arg-type]


def get_reservation(
    conn: sqlite3.Connection, reservation_id: int
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_RESERVATION_COLUMNS} FROM reservations WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    if row is None:
        return None
    return _attach_table(conn, _row_to_dict(row))


def get_reservation_by_code(
    conn: sqlite3.Connection, raw_code: str
) -> Optional[dict[str, Any]]:
    """Look up a reservation by its confirmation code. Case-insensitive,
    accepts dashes and spaces ('BUR-7K3' / 'bur 7k3' / 'BUR7K3')."""
    from app.codes import normalize_code
    code = normalize_code(raw_code)
    row = conn.execute(
        f"SELECT {_RESERVATION_COLUMNS} FROM reservations WHERE confirmation_code = ?",
        (code,),
    ).fetchone()
    if row is None:
        return None
    return _attach_table(conn, _row_to_dict(row))


def list_reservations(
    conn: sqlite3.Connection,
    phone: Optional[str] = None,
    status: Optional[str] = None,
    table_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if phone is not None:
        clauses.append("phone = ?")
        params.append(phone)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if table_id is not None:
        clauses.append("table_id = ?")
        params.append(table_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT {_RESERVATION_COLUMNS} FROM reservations {where} "
        "ORDER BY reservation_at ASC, id ASC",
        params,
    ).fetchall()
    return [_attach_table(conn, _row_to_dict(r)) for r in rows]


def update_reservation(
    conn: sqlite3.Connection, reservation_id: int, data: ReservationUpdate
) -> Optional[dict[str, Any]]:
    existing_row = conn.execute(
        f"SELECT {_RESERVATION_COLUMNS} FROM reservations WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    if existing_row is None:
        return None
    existing = _row_to_dict(existing_row)

    changes = data.model_dump(exclude_unset=True)

    new_party = changes.get("party_size", existing["party_size"])
    new_at_raw = changes.get("reservation_at", existing["reservation_at"])
    new_at = new_at_raw if isinstance(new_at_raw, datetime) else _parse_ts(new_at_raw)
    requested_table = changes.get("table_id", existing["table_id"])

    time_or_size_changed = (
        "party_size" in changes or "reservation_at" in changes or "table_id" in changes
    )
    if time_or_size_changed:
        try:
            resolved = _resolve_table_for(
                conn,
                requested_table_id=requested_table,
                party_size=new_party,
                at=new_at,
                exclude_reservation_id=reservation_id,
            )
        except ReservationError:
            if "table_id" in changes:
                raise
            resolved = _resolve_table_for(
                conn,
                requested_table_id=None,
                party_size=new_party,
                at=new_at,
                exclude_reservation_id=reservation_id,
            )
        changes["table_id"] = resolved

    if not changes:
        return _attach_table(conn, existing)

    sets: list[str] = []
    params: list[Any] = []
    for field, value in changes.items():
        if field == "reservation_at" and isinstance(value, datetime):
            value = _iso(value)
        sets.append(f"{field} = ?")
        params.append(value)
    sets.append("updated_at = datetime('now')")
    params.append(reservation_id)

    conn.execute(
        f"UPDATE reservations SET {', '.join(sets)} WHERE id = ?", params
    )
    return get_reservation(conn, reservation_id)


def cancel_reservation(
    conn: sqlite3.Connection, reservation_id: int
) -> Optional[dict[str, Any]]:
    existing = get_reservation(conn, reservation_id)
    if existing is None:
        return None
    if existing["status"] == "cancelled":
        return existing
    conn.execute(
        """
        UPDATE reservations
           SET status = 'cancelled', updated_at = datetime('now')
         WHERE id = ?
        """,
        (reservation_id,),
    )
    return get_reservation(conn, reservation_id)


# --- reviews -----------------------------------------------------------------

def list_reviews(
    conn: sqlite3.Connection,
    limit: int = 50,
    offset: int = 0,
    min_rating: Optional[int] = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if min_rating is not None:
        clauses.append("rating >= ?")
        params.append(min_rating)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])
    rows = conn.execute(
        f"SELECT {_REVIEW_COLUMNS} FROM reviews {where} "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _fetch_review_row(
    conn: sqlite3.Connection, review_id: int
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_REVIEW_COLUMNS} FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_review(
    conn: sqlite3.Connection, review_id: int
) -> Optional[dict[str, Any]]:
    review = _fetch_review_row(conn, review_id)
    if review is None:
        return None
    review["comments"] = list_review_comments(conn, review_id)
    return review


def create_review(
    conn: sqlite3.Connection, data: ReviewCreate
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO reviews (reviewer_name, reviewer_city, rating, body)
        VALUES (?, ?, ?, ?)
        """,
        (data.reviewer_name, data.reviewer_city, data.rating, data.body),
    )
    return get_review(conn, cur.lastrowid)  # type: ignore[arg-type, return-value]


def update_review(
    conn: sqlite3.Connection, review_id: int, data: ReviewUpdate
) -> Optional[dict[str, Any]]:
    if _fetch_review_row(conn, review_id) is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return get_review(conn, review_id)
    sets = [f"{k} = ?" for k in changes] + ["updated_at = datetime('now')"]
    params = list(changes.values()) + [review_id]
    conn.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE id = ?", params)
    return get_review(conn, review_id)


# --- review comments ---------------------------------------------------------

def list_review_comments(
    conn: sqlite3.Connection, review_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_REVIEW_COMMENT_COLUMNS} FROM review_comments "
        "WHERE review_id = ? ORDER BY created_at ASC, id ASC",
        (review_id,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_review_comment(
    conn: sqlite3.Connection, comment_id: int
) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_REVIEW_COMMENT_COLUMNS} FROM review_comments WHERE id = ?",
        (comment_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def create_review_comment(
    conn: sqlite3.Connection,
    review_id: int,
    data: ReviewCommentCreate,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO review_comments (review_id, author_role, author_name, body)
        VALUES (?, ?, ?, ?)
        """,
        (review_id, data.author_role, data.author_name, data.body),
    )
    return get_review_comment(conn, cur.lastrowid)  # type: ignore[arg-type, return-value]


def update_review_comment(
    conn: sqlite3.Connection,
    comment_id: int,
    data: ReviewCommentUpdate,
) -> Optional[dict[str, Any]]:
    if get_review_comment(conn, comment_id) is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        return get_review_comment(conn, comment_id)
    sets = [f"{k} = ?" for k in changes] + ["updated_at = datetime('now')"]
    params = list(changes.values()) + [comment_id]
    conn.execute(
        f"UPDATE review_comments SET {', '.join(sets)} WHERE id = ?", params
    )
    return get_review_comment(conn, comment_id)
