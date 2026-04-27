"""Spanish-language transactional message templates.

Date/time strings are formatted using the locale settings already in
`app/config.py` (`DATE_FORMAT`, `TIME_FORMAT`, `DATETIME_FORMAT`). The
confirmation code is rendered with the human-friendly dash (e.g. `BUR-7K3`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings

# Each template has a single `{room}` placeholder; for reservations not yet
# assigned to a room, render literal "el restaurante" instead.
CREATED = (
    "¡Reserva confirmada! Te esperamos el {date} a las {time} para "
    "{party} personas en {room}. Código de reserva: {code}. "
    "— La Posada de la Pepa"
)
UPDATED = (
    "Tu reserva {code} ha cambiado: {date} a las {time} para "
    "{party} personas en {room}. — La Posada de la Pepa"
)
CANCELLED = (
    "Tu reserva {code} para el {date} ha sido cancelada. "
    "Esperamos verte pronto. — La Posada de la Pepa"
)
REMINDER = (
    "Recordatorio: te esperamos mañana, {date} a las {time}, "
    "para {party} personas (reserva {code}). — La Posada de la Pepa"
)

_TEMPLATES = {
    "created": CREATED,
    "updated": UPDATED,
    "cancelled": CANCELLED,
    "reminder": REMINDER,
}


def _format_code(raw: str) -> str:
    """Insert the cosmetic dash for human consumption: 'BUR7K3' -> 'BUR-7K3'."""
    if len(raw) == 6:
        return f"{raw[:3]}-{raw[3:]}"
    return raw


def render(kind: str, reservation: dict[str, Any]) -> str:
    """Render one of the four templates.

    `reservation` is the dict shape produced by `crud.get_reservation()` — it
    carries `reservation_at` (ISO with offset), `party_size`, `confirmation_code`,
    and an optional nested `table.room.name`. Missing room renders as
    'el restaurante'.
    """
    if kind not in _TEMPLATES:
        raise ValueError(f"unknown notification kind: {kind!r}")

    settings = get_settings()
    tz = ZoneInfo(settings.timezone)

    raw_at = reservation["reservation_at"]
    if isinstance(raw_at, str):
        if raw_at.endswith("Z"):
            raw_at = raw_at[:-1] + "+00:00"
        when = datetime.fromisoformat(raw_at)
    else:
        when = raw_at
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz)
    when_local = when.astimezone(tz)

    table = reservation.get("table") or {}
    room = (table.get("room") or {}).get("name") or "el restaurante"

    return _TEMPLATES[kind].format(
        date=when_local.strftime(settings.date_format),
        time=when_local.strftime(settings.time_format),
        party=reservation["party_size"],
        room=room,
        code=_format_code(reservation["confirmation_code"]),
    )
