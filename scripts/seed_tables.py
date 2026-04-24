"""Seed the tables table and backfill confirmed reservations with table assignments.

Distribution (50 tables, 228 seats):
  capacity 2  -> 16 tables   (couples / duos)
  capacity 4  -> 16 tables   (workhorse)
  capacity 6  -> 10 tables   (medium groups)
  capacity 8  ->  5 tables   (large parties)
  capacity 10 ->  2 tables
  capacity 12 ->  1 table    (private / celebration)

Idempotent:
  - tables are only inserted if none exist.
  - reservations already assigned a table_id are left alone.

Assignment rules (must hold for every confirmed reservation):
  - table capacity >= party_size (smallest fitting table is preferred)
  - no other confirmed reservation on the same table within 2 hours

Usage:
    uv run python scripts/seed_tables.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import connection, init_db  # noqa: E402

DISTRIBUTION: list[tuple[int, int]] = [
    (2, 16),
    (4, 16),
    (6, 10),
    (8, 5),
    (10, 2),
    (12, 1),
]

CONFLICT_WINDOW = timedelta(hours=2)


def _parse_ts(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def ensure_tables(conn) -> int:
    existing = conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0]
    if existing > 0:
        return 0
    rows: list[tuple[str, int]] = []
    seq = 1
    for capacity, count in DISTRIBUTION:
        for _ in range(count):
            rows.append((f"T{seq:02d}", capacity))
            seq += 1
    conn.executemany(
        "INSERT INTO tables (table_number, capacity) VALUES (?, ?)", rows
    )
    return len(rows)


def backfill_assignments(conn) -> tuple[int, int]:
    tables = conn.execute(
        "SELECT id, capacity FROM tables ORDER BY capacity ASC, id ASC"
    ).fetchall()
    if not tables:
        raise RuntimeError("no tables exist — run ensure_tables first")

    # usage[table_id] = list of datetimes (for conflict check) + a total load count.
    usage: dict[int, list[datetime]] = {t["id"]: [] for t in tables}
    for row in conn.execute(
        "SELECT table_id, reservation_at FROM reservations "
        "WHERE status = 'confirmed' AND table_id IS NOT NULL"
    ).fetchall():
        usage[row["table_id"]].append(_parse_ts(row["reservation_at"]))

    unassigned = conn.execute(
        "SELECT id, party_size, reservation_at FROM reservations "
        "WHERE status = 'confirmed' AND table_id IS NULL "
        "ORDER BY reservation_at ASC, id ASC"
    ).fetchall()

    assigned = 0
    skipped = 0
    for row in unassigned:
        at = _parse_ts(row["reservation_at"])
        fitting = [t for t in tables if t["capacity"] >= row["party_size"]]
        # Smallest capacity first; within same capacity prefer least-loaded; ties by id.
        fitting.sort(key=lambda t: (t["capacity"], len(usage[t["id"]]), t["id"]))

        chosen: int | None = None
        for t in fitting:
            if any(abs(at - existing_at) < CONFLICT_WINDOW
                   for existing_at in usage[t["id"]]):
                continue
            chosen = t["id"]
            break

        if chosen is None:
            skipped += 1
            continue
        conn.execute(
            "UPDATE reservations SET table_id = ?, updated_at = datetime('now') WHERE id = ?",
            (chosen, row["id"]),
        )
        usage[chosen].append(at)
        assigned += 1
    return assigned, skipped


def main() -> int:
    init_db()
    with connection() as conn:
        inserted = ensure_tables(conn)
        assigned, skipped = backfill_assignments(conn)
        total_tables = conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0]
        confirmed_total = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE status = 'confirmed'"
        ).fetchone()[0]
        assigned_total = conn.execute(
            "SELECT COUNT(*) FROM reservations "
            "WHERE status = 'confirmed' AND table_id IS NOT NULL"
        ).fetchone()[0]

    print(f"tables: {total_tables} total ({inserted} newly inserted)")
    print(f"confirmed reservations: {confirmed_total}")
    print(f"  assigned a table:    {assigned_total}")
    print(f"  newly assigned:      {assigned}")
    print(f"  could not place:     {skipped}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
