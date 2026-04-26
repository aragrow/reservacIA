"""Confirmation codes — short, human-friendly, PNR-style identifiers.

Codes are 6 characters drawn from a 27-character alphabet that drops the
visually-confusable glyphs:

  A C D E F G H J K M N P Q R S T W X Y Z   (20 letters — minus B I L O U V)
  2 3 4 5 6 7 9                              ( 7 digits  — minus 0 1 8)

That gives 27**6 ≈ 387 M codes. Plenty for any single restaurant; collisions
are vanishingly rare and handled by retry on UNIQUE violation.

Codes are *low-security identifiers*, not passwords. They're safe to print on
a receipt or read aloud. Use them to *find* a reservation; do not treat them
as authentication for mutating operations — that still requires the JWT and
should eventually require phone-OTP.
"""
from __future__ import annotations

import secrets

ALPHABET = "ACDEFGHJKMNPQRSTWXYZ2345679"  # 27 chars
CODE_LENGTH = 6


def generate_code() -> str:
    """Return one randomly-chosen 6-character code (uppercase, no separators)."""
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str:
    """Canonicalise user-entered codes for lookup.

    Accepts any case, plus optional dashes/spaces between groups so customers
    can type 'BUR-7K3' or 'bur 7k3' or 'BUR7K3' — all collapse to the
    canonical 'BUR7K3'.
    """
    cleaned = "".join(ch for ch in raw.upper() if ch not in "- ")
    return cleaned


def is_well_formed(raw: str) -> bool:
    """Cheap pre-DB syntax check: right length, only alphabet chars."""
    code = normalize_code(raw)
    return len(code) == CODE_LENGTH and all(ch in ALPHABET for ch in code)
