"""Import reviews from the hand-written resenas.md into the reviews table.

Each entry in resenas.md has the shape:

    ## N. <reviewer name> — ★★★★★
    *<City>, <Country> · <date>*

    <body paragraph(s)>

    > **Respuesta del restaurante**: <reply paragraph(s)>
    >
    > (continued)

    ---

We create one row in `reviews` per entry and (if a response blockquote is
present) one row in `review_comments` with author_role='restaurant'.

Idempotent: if any reviews already exist in the DB, the script bails out
to avoid duplicates. Delete the existing rows first (or wipe the DB) if
you want to re-run.

Usage:
    uv run python scripts/import_resenas.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import connection, init_db  # noqa: E402

RESENAS = ROOT / "resenas.md"

HEADER_RE = re.compile(r"^##\s+\d+\.\s+(.+?)\s+—\s+(★+)(☆*)\s*$")
META_RE = re.compile(r"^\*(.+?)\s·\s(.+?)\*\s*$")  # city, country · date


def _parse() -> list[dict]:
    """Return a list of {reviewer_name, reviewer_city, rating, body, reply}."""
    text = RESENAS.read_text(encoding="utf-8")
    # Split on a line that is just '---' (bracketed by blank lines) — that's the
    # separator between entries. Use a simple scanner since markdown also has
    # '---' inside the header section.
    lines = text.splitlines()

    entries: list[dict] = []
    i = 0
    while i < len(lines):
        m = HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1).strip()
        rating = len(m.group(2))  # count of filled stars
        i += 1
        # Next non-blank line should be the italic metadata line.
        while i < len(lines) and not lines[i].strip():
            i += 1
        meta = META_RE.match(lines[i]) if i < len(lines) else None
        city = meta.group(1).strip() if meta else None
        if meta:
            i += 1

        # Collect body (until the first '>' or '---') and reply (all '>' lines).
        body_lines: list[str] = []
        reply_lines: list[str] = []
        mode = "body"
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if stripped == "---":
                i += 1
                break
            if stripped.startswith(">"):
                mode = "reply"
                # Strip one leading '>' and optional space.
                reply_lines.append(re.sub(r"^>\s?", "", line))
            elif mode == "body":
                body_lines.append(line)
            else:
                # Blank line inside reply — keep paragraph spacing.
                if not stripped:
                    reply_lines.append("")
                else:
                    # Line after reply that isn't a blockquote: end of entry.
                    break
            i += 1

        body = _collapse(body_lines)
        reply = _collapse(reply_lines)
        # Trim the leading "**Respuesta del restaurante**: " from the reply.
        reply = re.sub(r"^\*\*Respuesta del restaurante\*\*:\s*", "", reply)

        entries.append({
            "reviewer_name": name,
            "reviewer_city": city,
            "rating": rating,
            "body": body,
            "reply": reply or None,
        })
    return entries


def _collapse(lines: list[str]) -> str:
    """Collapse a list of raw lines into paragraph-wrapped text."""
    # Drop leading/trailing blank lines.
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    # Join consecutive non-blank lines with a single space (markdown hard-wrap),
    # preserve blank lines as paragraph breaks.
    out_paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line.strip())
        else:
            if current:
                out_paragraphs.append(" ".join(current))
                current = []
    if current:
        out_paragraphs.append(" ".join(current))
    return "\n\n".join(out_paragraphs)


def main() -> int:
    init_db()
    entries = _parse()
    print(f"parsed {len(entries)} reviews from {RESENAS.name}")

    with connection() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        if existing > 0:
            print(f"refusing to import: {existing} review(s) already in DB")
            return 1

        inserted = 0
        with_reply = 0
        for e in entries:
            cur = conn.execute(
                """
                INSERT INTO reviews (reviewer_name, reviewer_city, rating, body)
                VALUES (?, ?, ?, ?)
                """,
                (e["reviewer_name"], e["reviewer_city"], e["rating"], e["body"]),
            )
            inserted += 1
            if e["reply"]:
                conn.execute(
                    """
                    INSERT INTO review_comments
                        (review_id, author_role, author_name, body)
                    VALUES (?, 'restaurant', ?, ?)
                    """,
                    (cur.lastrowid, "La Posada de la Pepa", e["reply"]),
                )
                with_reply += 1

        totals = conn.execute(
            "SELECT COUNT(*) AS n, AVG(rating) AS avg FROM reviews"
        ).fetchone()
        by_rating = conn.execute(
            "SELECT rating, COUNT(*) FROM reviews GROUP BY rating ORDER BY rating DESC"
        ).fetchall()

    print(f"inserted {inserted} reviews ({with_reply} with a restaurant reply)")
    print(f"average rating: {totals['avg']:.2f}")
    print("by rating:")
    for row in by_rating:
        print(f"  {row['rating']}★  {row[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
