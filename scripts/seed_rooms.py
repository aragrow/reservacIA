"""Seed the four restaurant rooms and assign every table to one.

Distribution (50 tables across 4 rooms):
  Bar             10 tables  (T01-T10, all capacity 2 — stools by the bar)
  Booths          12 tables  (T11-T16 cap-2 + T17-T22 cap-4)
  Dining Room 1   14 tables  (mix of cap-4/6/8 + the 12-top for big parties)
  Dining Room 2   14 tables  (mix of cap-4/6/8 + both 10-tops)

Idempotent:
  - rooms are only inserted if the room table is empty.
  - tables already assigned a room_id are left alone.

Usage:
    uv run python scripts/seed_rooms.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import connection, init_db  # noqa: E402

ROOMS: list[tuple[str, str]] = [
    ("Bar", "Stools and high-tops along the bar"),
    ("Booths", "Banquette seating along the wall"),
    ("Dining Room 1", "Main dining room — varied table sizes, hosts private parties"),
    ("Dining Room 2", "Secondary dining room — varied table sizes, hosts larger groups"),
]


def _room_id_by_name(conn) -> dict[str, int]:
    return {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM rooms")}


def ensure_rooms(conn) -> int:
    existing = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
    if existing > 0:
        return 0
    conn.executemany(
        "INSERT INTO rooms (name, description) VALUES (?, ?)", ROOMS
    )
    return len(ROOMS)


def assign_tables(conn) -> tuple[int, int]:
    """Assign each table a room_id according to the plan above."""
    rooms = _room_id_by_name(conn)
    required = {"Bar", "Booths", "Dining Room 1", "Dining Room 2"}
    missing = required - rooms.keys()
    if missing:
        raise RuntimeError(f"rooms missing from DB: {missing}")

    # Tables are inserted in order of (capacity, id) by seed_tables.py, so table
    # IDs 1..50 correspond to increasing capacity. Bucket by id range + parity.
    plan: dict[int, int] = {}

    # T01-T10 -> Bar
    for tid in range(1, 11):
        plan[tid] = rooms["Bar"]

    # T11-T22 -> Booths (6 cap-2 + 6 cap-4)
    for tid in range(11, 23):
        plan[tid] = rooms["Booths"]

    # T23-T32 (cap-4): split 5/5 between Dining 1 and Dining 2 (odd/even)
    # T33-T42 (cap-6): split 5/5 likewise
    # T43-T47 (cap-8): 3 to Dining 1, 2 to Dining 2 (by id order)
    # T48-T49 (cap-10): both to Dining 2
    # T50 (cap-12): to Dining 1
    for tid in range(23, 33):          # cap-4, odd -> D1, even -> D2
        plan[tid] = rooms["Dining Room 1"] if tid % 2 == 1 else rooms["Dining Room 2"]
    for tid in range(33, 43):          # cap-6, odd -> D1, even -> D2
        plan[tid] = rooms["Dining Room 1"] if tid % 2 == 1 else rooms["Dining Room 2"]
    for tid in (43, 45, 47):           # cap-8 -> Dining 1
        plan[tid] = rooms["Dining Room 1"]
    for tid in (44, 46):               # cap-8 -> Dining 2
        plan[tid] = rooms["Dining Room 2"]
    for tid in (48, 49):               # cap-10 -> Dining 2
        plan[tid] = rooms["Dining Room 2"]
    plan[50] = rooms["Dining Room 1"]  # cap-12 -> Dining 1

    assigned = 0
    skipped = 0
    for tid, room_id in plan.items():
        row = conn.execute(
            "SELECT room_id FROM tables WHERE id = ?", (tid,)
        ).fetchone()
        if row is None:
            skipped += 1
            continue
        if row["room_id"] is not None:
            skipped += 1
            continue
        conn.execute("UPDATE tables SET room_id = ? WHERE id = ?", (room_id, tid))
        assigned += 1
    return assigned, skipped


def main() -> int:
    init_db()
    with connection() as conn:
        inserted = ensure_rooms(conn)
        assigned, skipped = assign_tables(conn)

        print(f"rooms inserted:        {inserted}")
        print(f"tables newly assigned: {assigned}")
        print(f"tables left alone:     {skipped}")
        print()
        print("final distribution:")
        for row in conn.execute("""
            SELECT r.name, COUNT(t.id) AS n,
                   GROUP_CONCAT(t.capacity, ',') AS caps
              FROM rooms r LEFT JOIN tables t ON t.room_id = r.id
             GROUP BY r.id ORDER BY r.id
        """):
            caps = sorted(int(c) for c in (row["caps"] or "").split(",") if c)
            cap_summary = ", ".join(
                f"{n}×cap-{c}" for c, n in _count_by(caps).items()
            ) or "empty"
            print(f"  {row['name']:<16} {row['n']:>2} tables   [{cap_summary}]")
    return 0


def _count_by(values):
    out: dict[int, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
