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

    tbl_cols = {row[1] for row in conn.execute("PRAGMA table_info(tables)").fetchall()}
    if "room_id" not in tbl_cols:
        conn.execute("ALTER TABLE tables ADD COLUMN room_id INTEGER REFERENCES rooms(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tables_room ON tables(room_id)")


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
