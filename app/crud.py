from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from app.models import ReservationCreate, ReservationUpdate

CONFLICT_WINDOW = timedelta(hours=2)  # no two reservations on same table within 2h

_RESERVATION_COLUMNS = (
    "id, phone, customer_name, party_size, reservation_at, notes, status, "
    "table_id, created_at, updated_at"
)
_TABLE_COLUMNS = "id, table_number, capacity, created_at"


class ReservationError(Exception):
    """Raised for domain-level validation failures (mapped to 409 Conflict)."""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_ts(value: str) -> datetime:
    # Handle trailing 'Z' which fromisoformat supports only from 3.11+.
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


# --- tables ------------------------------------------------------------------

def list_tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_TABLE_COLUMNS} FROM tables ORDER BY capacity ASC, id ASC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_table(conn: sqlite3.Connection, table_id: int) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {_TABLE_COLUMNS} FROM tables WHERE id = ?", (table_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


# --- conflict detection ------------------------------------------------------

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


def find_available_table(
    conn: sqlite3.Connection,
    party_size: int,
    at: datetime,
    exclude_reservation_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Return the smallest-capacity table that fits and has no 2h conflict.

    Ties (same capacity) are broken by current load — preferring the least-used
    table — so assignments spread across the floor instead of piling onto the
    lowest id.
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
            return {k: row[k] for k in ("id", "table_number", "capacity", "created_at")}
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
    cur = conn.execute(
        """
        INSERT INTO reservations
            (phone, customer_name, party_size, reservation_at, notes, table_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (data.phone, data.customer_name, data.party_size, _iso(at), data.notes, table_id),
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

    # Resolve the target state after the patch so we can revalidate table usage.
    new_party = changes.get("party_size", existing["party_size"])
    new_at_raw = changes.get("reservation_at", existing["reservation_at"])
    new_at = new_at_raw if isinstance(new_at_raw, datetime) else _parse_ts(new_at_raw)
    requested_table = changes.get("table_id", existing["table_id"])

    # Only re-check table when something time/size/table-related moves, OR when
    # status is confirmed (which for updates is always true — cancellation is a
    # separate endpoint).
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
            # Caller didn't explicitly choose — auto-reassign.
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
