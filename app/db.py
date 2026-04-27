from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    description     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    table_number    TEXT    NOT NULL UNIQUE,
    capacity        INTEGER NOT NULL CHECK (capacity BETWEEN 2 AND 12),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tables_capacity ON tables(capacity);

CREATE TABLE IF NOT EXISTS reservations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    phone           TEXT    NOT NULL,
    customer_name   TEXT    NOT NULL,
    party_size      INTEGER NOT NULL CHECK (party_size > 0),
    reservation_at  TEXT    NOT NULL,
    notes           TEXT,
    status          TEXT    NOT NULL DEFAULT 'confirmed'
                            CHECK (status IN ('confirmed','cancelled')),
    table_id        INTEGER REFERENCES tables(id),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reservations_phone    ON reservations(phone);
CREATE INDEX IF NOT EXISTS idx_reservations_status   ON reservations(status);

CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    reviewer_name  TEXT    NOT NULL,
    reviewer_city  TEXT,
    rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    body           TEXT    NOT NULL,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reviews_rating   ON reviews(rating);
CREATE INDEX IF NOT EXISTS idx_reviews_created  ON reviews(created_at DESC);

CREATE TABLE IF NOT EXISTS review_comments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id    INTEGER NOT NULL REFERENCES reviews(id),
    author_role  TEXT    NOT NULL CHECK (author_role IN ('restaurant','customer')),
    author_name  TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_review_comments_review ON review_comments(review_id, created_at);

CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id  INTEGER REFERENCES reservations(id),
    kind            TEXT    NOT NULL CHECK (kind IN
                            ('created','updated','cancelled','reminder','custom')),
    phone           TEXT    NOT NULL,
    scheduled_at    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN
                            ('pending','in_flight','sent','failed','cancelled')),
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    body            TEXT    NOT NULL,
    sent_at         TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notifications_due
    ON notifications(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_notifications_reservation
    ON notifications(reservation_id);
"""
# Indexes on late-added columns (`table_id`, `room_id`) live in _migrate()
# so pre-existing databases get the columns before the indexes try to use them.


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    res_cols = {row[1] for row in conn.execute("PRAGMA table_info(reservations)").fetchall()}
    if "table_id" not in res_cols:
        conn.execute("ALTER TABLE reservations ADD COLUMN table_id INTEGER REFERENCES tables(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reservations_table ON reservations(table_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reservations_time  ON reservations(reservation_at)")

    if "confirmation_code" not in res_cols:
        # Add column nullable initially — the lazy backfill below populates any
        # NULLs every startup, so every row ends up with a code.
        conn.execute("ALTER TABLE reservations ADD COLUMN confirmation_code TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_reservations_code "
        "ON reservations(confirmation_code) WHERE confirmation_code IS NOT NULL"
    )

    _migrate_notifications_table(conn)

    tbl_cols = {row[1] for row in conn.execute("PRAGMA table_info(tables)").fetchall()}
    if "room_id" not in tbl_cols:
        conn.execute("ALTER TABLE tables ADD COLUMN room_id INTEGER REFERENCES rooms(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tables_room ON tables(room_id)")

    _backfill_confirmation_codes(conn)


def _migrate_notifications_table(conn: sqlite3.Connection) -> None:
    """Rebuild `notifications` if it predates the agent-driven additions.

    Two old shapes to fix in one pass:
      - `reservation_id INTEGER NOT NULL`  →  becomes nullable so the agent
         can post notifications not tied to any reservation.
      - kind CHECK without `'custom'`      →  add the new kind so agent
         messages don't have to pretend to be a lifecycle event.

    SQLite can't change either constraint in place, so this rebuilds the table.
    Idempotent: skips when the stored CREATE statement already contains 'custom'.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='notifications'"
    ).fetchone()
    if row is None:
        # Table will be created fresh from SCHEMA — nothing to migrate.
        return
    create_sql = row["sql"] or ""
    if "'custom'" in create_sql:
        # Already on the new shape.
        return

    conn.execute("ALTER TABLE notifications RENAME TO _notifications_old")
    conn.executescript(
        """
        CREATE TABLE notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id  INTEGER REFERENCES reservations(id),
            kind            TEXT    NOT NULL CHECK (kind IN
                                    ('created','updated','cancelled','reminder','custom')),
            phone           TEXT    NOT NULL,
            scheduled_at    TEXT    NOT NULL,
            status          TEXT    NOT NULL DEFAULT 'pending'
                                    CHECK (status IN
                                    ('pending','in_flight','sent','failed','cancelled')),
            attempts        INTEGER NOT NULL DEFAULT 0,
            last_error      TEXT,
            body            TEXT    NOT NULL,
            sent_at         TEXT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_due
            ON notifications(status, scheduled_at);
        CREATE INDEX IF NOT EXISTS idx_notifications_reservation
            ON notifications(reservation_id);
        """
    )
    conn.execute(
        """
        INSERT INTO notifications
            (id, reservation_id, kind, phone, scheduled_at, status, attempts,
             last_error, body, sent_at, created_at, updated_at)
        SELECT id, reservation_id, kind, phone, scheduled_at, status, attempts,
               last_error, body, sent_at, created_at, updated_at
          FROM _notifications_old
        """
    )
    conn.execute("DROP TABLE _notifications_old")


def _backfill_confirmation_codes(conn: sqlite3.Connection) -> int:
    """Populate confirmation_code on every reservation that doesn't have one.

    Runs on every init_db() call but is cheap when there's nothing to do
    (single LIMIT-1 probe). Returns the number of rows updated."""
    has_null = conn.execute(
        "SELECT 1 FROM reservations WHERE confirmation_code IS NULL LIMIT 1"
    ).fetchone()
    if has_null is None:
        return 0
    # Local import to avoid a top-level cycle (codes has no other deps).
    from app.codes import generate_code
    nulls = conn.execute(
        "SELECT id FROM reservations WHERE confirmation_code IS NULL"
    ).fetchall()
    updated = 0
    for row in nulls:
        # Tiny retry loop — collisions on a 387M-key space are vanishingly rare.
        for _ in range(8):
            code = generate_code()
            try:
                conn.execute(
                    "UPDATE reservations SET confirmation_code = ? WHERE id = ?",
                    (code, row[0]),
                )
                updated += 1
                break
            except sqlite3.IntegrityError:
                continue
    return updated


def init_db() -> None:
    settings = get_settings()
    path = settings.database_path
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = _connect(settings.database_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
