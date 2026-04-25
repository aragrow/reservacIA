"""Seed the reservations DB with realistic demo data.

Layout:
  - 75 named customers participate in PAST reservations.
  - 100 past reservations are distributed across those 75 (some customers have
    more than one prior visit).
  - 100 FUTURE reservations are created across a mix of those 75 existing
    customers + a handful of new ones. A subset of customers get two future
    reservations exactly 14 days apart.

The DB file used is whatever DATABASE_PATH points at (loaded from .env via the
app config). The script is deterministic (seeded) but NOT idempotent — running
it twice will double the data.

Usage:
    uv run python scripts/seed.py
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import connection, init_db  # noqa: E402

RNG_SEED = 20260423
NUM_PAST_CUSTOMERS = 75
NUM_PAST_RESERVATIONS = 100
NUM_FUTURE_RESERVATIONS = 100
NUM_NEW_FUTURE_CUSTOMERS = 20
FUTURE_REPEAT_COUNT = 30          # customers with 2 future reservations 14 days apart
REPEAT_GAP_DAYS = 14

FIRST_NAMES = [
    "Ana", "Luis", "María", "Carlos", "Sofía", "Diego", "Valentina", "Javier",
    "Camila", "Andrés", "Isabella", "Miguel", "Lucía", "Emilio", "Paula",
    "Gabriel", "Renata", "Mateo", "Daniela", "Sebastián", "Natalia", "Tomás",
    "Alejandra", "Rodrigo", "Elena", "Santiago", "Fernanda", "Nicolás", "Gabriela",
    "Ricardo", "Paola", "Óscar", "Carolina", "Martín", "Adriana", "Fernando",
    "Mónica", "Héctor", "Beatriz", "Pablo", "Claudia", "Jorge", "Victoria",
    "Roberto", "Raquel", "Arturo", "Teresa", "Emma", "Hugo", "Sara",
    "Mía", "Amelia", "Noé", "Olivia", "Iván", "Inés", "Bruno",
]
LAST_NAMES = [
    "García", "Rodríguez", "Martínez", "López", "González", "Pérez", "Sánchez",
    "Ramírez", "Torres", "Flores", "Rivera", "Gómez", "Díaz", "Reyes", "Morales",
    "Cruz", "Ortiz", "Gutiérrez", "Chávez", "Ruiz", "Álvarez", "Mendoza",
    "Vargas", "Castillo", "Jiménez", "Moreno", "Romero", "Herrera", "Medina",
    "Aguilar",
]
NOTES_POOL = [
    None, None, None,
    "mesa junto a la ventana", "trona", "cumpleaños", "aniversario",
    "sin gluten", "alergia a frutos secos", "acceso silla de ruedas",
    "mesa tranquila", "preferencia patio", "alzador",
]


def make_customer(used_phones: set[str], rng: random.Random) -> tuple[str, str]:
    name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    while True:
        phone = "+1" + "".join(str(rng.randint(0, 9)) for _ in range(10))
        if phone not in used_phones:
            used_phones.add(phone)
            return name, phone


def random_time_on(day: datetime, rng: random.Random) -> datetime:
    # Dinner window 17:00 - 21:30, on the half hour.
    hour = rng.choice([17, 18, 18, 19, 19, 19, 20, 20, 21])
    minute = rng.choice([0, 30])
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def main() -> int:
    rng = random.Random(RNG_SEED)
    init_db()

    # All reservation timestamps are stored in the restaurant's local timezone
    # (Europe/Madrid by default) — see .env / app/config.py.
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz=tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- 75 past customers ---
    used_phones: set[str] = set()
    past_customers = [make_customer(used_phones, rng) for _ in range(NUM_PAST_CUSTOMERS)]

    # --- 100 past reservations distributed across the 75 ---
    # Ensure every past customer appears at least once, then draw the rest.
    draws = list(range(NUM_PAST_CUSTOMERS))
    while len(draws) < NUM_PAST_RESERVATIONS:
        draws.append(rng.randrange(NUM_PAST_CUSTOMERS))
    rng.shuffle(draws)

    past_rows: list[tuple] = []
    for idx in draws:
        name, phone = past_customers[idx]
        days_ago = rng.randint(1, 365)
        when = random_time_on(today - timedelta(days=days_ago), rng)
        party = rng.choices([2, 2, 3, 4, 4, 5, 6, 8], k=1)[0]
        notes = rng.choice(NOTES_POOL)
        # Some past reservations will be cancelled (realistic noise, ~10%).
        status = "cancelled" if rng.random() < 0.10 else "confirmed"
        past_rows.append((phone, name, party, when.isoformat(), notes, status))

    # --- Future reservations ---
    # Subset of past customers who will come back twice (14 days apart).
    repeat_indices = rng.sample(range(NUM_PAST_CUSTOMERS), FUTURE_REPEAT_COUNT)
    future_rows: list[tuple] = []

    for idx in repeat_indices:
        name, phone = past_customers[idx]
        first_day_offset = rng.randint(3, 60)  # first visit 3-60 days out
        first = random_time_on(today + timedelta(days=first_day_offset), rng)
        second = random_time_on(
            today + timedelta(days=first_day_offset + REPEAT_GAP_DAYS), rng
        )
        party1 = rng.choices([2, 2, 3, 4, 4, 5], k=1)[0]
        party2 = rng.choices([2, 2, 3, 4, 4, 5], k=1)[0]
        future_rows.append((phone, name, party1, first.isoformat(),
                            rng.choice(NOTES_POOL), "confirmed"))
        future_rows.append((phone, name, party2, second.isoformat(),
                            rng.choice(NOTES_POOL), "confirmed"))

    # Now fill remaining future reservations (100 - 2*repeats) with single bookings.
    remaining = NUM_FUTURE_RESERVATIONS - 2 * FUTURE_REPEAT_COUNT  # 40
    # Mix: some new customers, some existing past customers not already in repeat set.
    new_customers = [make_customer(used_phones, rng) for _ in range(NUM_NEW_FUTURE_CUSTOMERS)]
    non_repeat_past = [past_customers[i] for i in range(NUM_PAST_CUSTOMERS) if i not in set(repeat_indices)]
    singletons_pool = new_customers + non_repeat_past
    rng.shuffle(singletons_pool)

    for i in range(remaining):
        name, phone = singletons_pool[i % len(singletons_pool)]
        day_offset = rng.randint(1, 90)
        when = random_time_on(today + timedelta(days=day_offset), rng)
        party = rng.choices([2, 2, 3, 4, 4, 5, 6, 8], k=1)[0]
        future_rows.append((phone, name, party, when.isoformat(),
                            rng.choice(NOTES_POOL), "confirmed"))

    rng.shuffle(future_rows)

    # --- Insert ---
    with connection() as conn:
        conn.executemany(
            """
            INSERT INTO reservations
                (phone, customer_name, party_size, reservation_at, notes, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            past_rows + future_rows,
        )
        past_count = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE reservation_at < datetime('now')"
        ).fetchone()[0]
        future_count = conn.execute(
            "SELECT COUNT(*) FROM reservations WHERE reservation_at >= datetime('now')"
        ).fetchone()[0]
        distinct_phones = conn.execute(
            "SELECT COUNT(DISTINCT phone) FROM reservations"
        ).fetchone()[0]
        repeat_future_phones = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT phone FROM reservations
              WHERE reservation_at >= datetime('now')
              GROUP BY phone HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]

    print(f"Inserted {len(past_rows)} past + {len(future_rows)} future rows")
    print(f"  past reservations in DB:     {past_count}")
    print(f"  future reservations in DB:   {future_count}")
    print(f"  distinct phone numbers:      {distinct_phones}")
    print(f"  phones with >1 future visit: {repeat_future_phones}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
