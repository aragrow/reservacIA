"""One-shot backfill: convert mixed-language reservation rows to Spanish/Madrid.

Three things get fixed:

1. **Customer names** — un-accented forms ("Andres Jimenez") are rewritten
   to the proper accented spelling ("Andrés Jiménez"). Names not in the
   map below are left alone.
2. **Notes** — English placeholder strings ("window seat") are translated
   to their Spanish equivalents. Anything outside the map is left alone.
3. **`reservation_at` timestamps** — the wall-clock numbers in the DB were
   generated for a Spanish dinner audience (17:00–21:30) but stored with
   a `+00:00` (UTC) suffix. Per the new convention "the DB is in
   Europe/Madrid", we strip the wrong offset and re-emit each timestamp
   with the proper Madrid offset for that specific date (CET/CEST).

Idempotent — running it twice is a no-op (Spanish forms are detected and
left alone the second time).

Usage:
    uv run python scripts/backfill_spanish.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import connection  # noqa: E402

# Mapping of un-accented -> accented Spanish names. Anything not listed is
# either already accented, intentionally non-Spanish (e.g., John, Yuki), or
# language-agnostic and can stay as-is.
NAME_FIXES: dict[str, str] = {
    # First names
    "Andres": "Andrés",
    "Maria": "María",
    "Sofia": "Sofía",
    "Lucia": "Lucía",
    "Sebastian": "Sebastián",
    "Tomas": "Tomás",
    "Nicolas": "Nicolás",
    "Oscar": "Óscar",
    "Martin": "Martín",
    "Monica": "Mónica",
    "Hector": "Héctor",
    "Mia": "Mía",
    "Ivan": "Iván",
    "Ines": "Inés",
    "Noe": "Noé",
    # Last names
    "Garcia": "García",
    "Rodriguez": "Rodríguez",
    "Martinez": "Martínez",
    "Lopez": "López",
    "Gonzalez": "González",
    "Perez": "Pérez",
    "Sanchez": "Sánchez",
    "Ramirez": "Ramírez",
    "Gomez": "Gómez",
    "Diaz": "Díaz",
    "Gutierrez": "Gutiérrez",
    "Chavez": "Chávez",
    "Alvarez": "Álvarez",
    "Jimenez": "Jiménez",
    "Nunez": "Núñez",
}

# English notes -> Spanish. Everything else (already-Spanish strings, NULL,
# free-text from later edits) is left alone.
NOTE_FIXES: dict[str, str] = {
    "window seat": "mesa junto a la ventana",
    "high chair": "trona",
    "birthday": "cumpleaños",
    "anniversary": "aniversario",
    "gluten free": "sin gluten",
    "peanut allergy": "alergia a frutos secos",
    "wheelchair access": "acceso silla de ruedas",
    "quiet table": "mesa tranquila",
    "patio preferred": "preferencia patio",
    "booster seat": "alzador",
}


def fix_name(name: str) -> str:
    """Rewrite each whitespace-separated token through NAME_FIXES."""
    parts = name.split()
    return " ".join(NAME_FIXES.get(p, p) for p in parts)


def fix_note(note: str | None) -> str | None:
    if note is None:
        return None
    return NOTE_FIXES.get(note, note)


_OFFSET_RE = re.compile(r"([+\-]\d{2}:\d{2}|Z)$")


def reinterpret_as_madrid(stored: str, tz: ZoneInfo) -> str:
    """Strip any timezone suffix and re-emit the wall-clock as Madrid local.

    Examples (assuming Europe/Madrid):
      '2026-05-01T19:30:00+00:00' -> '2026-05-01T19:30:00+02:00'  (CEST in May)
      '2026-01-15T20:00:00+00:00' -> '2026-01-15T20:00:00+01:00'  (CET in January)
      '2026-05-01T19:30:00'       -> '2026-05-01T19:30:00+02:00'  (already naive)

    Wall-clock digits don't move — this is purely a re-labeling.
    """
    naive_str = _OFFSET_RE.sub("", stored)
    naive = datetime.fromisoformat(naive_str)
    aware = naive.replace(tzinfo=tz)
    return aware.isoformat()


def main() -> int:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)

    fixed_names = 0
    fixed_notes = 0
    fixed_times = 0

    with connection() as conn:
        rows = conn.execute(
            "SELECT id, customer_name, notes, reservation_at FROM reservations"
        ).fetchall()
        for row in rows:
            new_name = fix_name(row["customer_name"])
            new_note = fix_note(row["notes"])
            new_at = reinterpret_as_madrid(row["reservation_at"], tz)

            sets, params = [], []
            if new_name != row["customer_name"]:
                sets.append("customer_name = ?")
                params.append(new_name)
                fixed_names += 1
            if new_note != row["notes"]:
                sets.append("notes = ?")
                params.append(new_note)
                fixed_notes += 1
            if new_at != row["reservation_at"]:
                sets.append("reservation_at = ?")
                params.append(new_at)
                fixed_times += 1
            if sets:
                # Don't bump updated_at — this is a data correction, not a
                # logical edit; preserving the original audit timestamp is
                # more honest.
                params.append(row["id"])
                conn.execute(
                    f"UPDATE reservations SET {', '.join(sets)} WHERE id = ?",
                    params,
                )

    print(f"reservations scanned: {len(rows)}")
    print(f"  customer_name fixes: {fixed_names}")
    print(f"  notes fixes:         {fixed_notes}")
    print(f"  reservation_at fixes:{fixed_times}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
