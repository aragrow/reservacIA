# Confirmation codes — implementation notes & handoff

This is the contract for the agent / frontend / any other consumer of reservacIA. Use it as input when prompting another agent or briefing a teammate.

## What was built

A short, human-friendly identifier (PNR-style) is now generated for every reservation. It's a **convenience identifier**, not authentication — anyone with the code can look up the booking, but mutations still require the agent JWT (and eventually phone-OTP, separate workstream).

### New files

- [`app/codes.py`](../app/codes.py) — alphabet, generator, normalizer. Three exports: `ALPHABET`, `generate_code()`, `normalize_code(raw)`.
- [`tests/test_confirmation_code.py`](../tests/test_confirmation_code.py) — 12 tests covering shape, uniqueness, lookup variants, immutability, backfill.
- [`docs/confirmation-code.md`](confirmation-code.md) — this file.

### Modified files

- [`app/db.py`](../app/db.py) — `_migrate()` adds `confirmation_code TEXT` column, partial UNIQUE index on non-null values, and runs `_backfill_confirmation_codes()` on every `init_db()` (cheap when no NULLs).
- [`app/crud.py`](../app/crud.py) — `_RESERVATION_COLUMNS` includes the new field; `_generate_unique_code()` retries on collision; `create_reservation()` mints the code at insert; `get_reservation_by_code()` looks up by normalized code.
- [`app/models.py`](../app/models.py) — `ReservationOut.confirmation_code: str` is now part of every reservation response.
- [`app/routers/reservations.py`](../app/routers/reservations.py) — new `GET /reservations/by-code/{code}` endpoint, declared **before** the `/{reservation_id}` route to avoid path collisions.

## Code format

- **6 characters** drawn from a 27-char alphabet: `ACDEFGHJKMNPQRSTWXYZ2345679`
- Drops visually confusable glyphs: `B`, `I`, `L`, `O`, `U`, `V`, `0`, `1`, `8`
- Stored in DB as the canonical 6-char uppercase string (no separators)
- Total key space: 27<sup>6</sup> ≈ 387 million codes
- Generated via `secrets.choice` (cryptographic RNG) with a retry loop on `UNIQUE` violation — collisions are vanishingly rare in practice

## API contract

### Create — POST /reservations

The response now includes `confirmation_code`:

```json
{
  "id": 206,
  "phone": "+15551234567",
  "customer_name": "Andrés Diaz",
  "party_size": 2,
  "reservation_at": "2027-08-15T19:30:00+02:00",
  "status": "confirmed",
  "table_id": 7,
  "table": { "...": "..." },
  "confirmation_code": "6DNT7W",
  "created_at": "...",
  "updated_at": "..."
}
```

The code is **server-assigned**. The client cannot supply or override it on POST. Attempting to do so returns 422 (the request model has `extra="forbid"` on PATCH and ignores unknown fields on POST).

### Lookup — GET /reservations/by-code/{code}

```
GET /reservations/by-code/6DNT7W      → 200 + the reservation row
GET /reservations/by-code/6dnt7w      → 200 (case-insensitive)
GET /reservations/by-code/6DN-T7W     → 200 (dashes/spaces stripped)
GET /reservations/by-code/ZZZZZZ      → 404 {"detail": "reservation not found"}
GET /reservations/by-code/INVALID0    → 404 (malformed codes return same shape)
```

- **Authentication**: still requires the existing Bearer JWT — same as every other `/reservations/*` route. Codes do not bypass auth; they're just an alternative way to identify a row.
- **Case insensitive**: `BUR-7K3`, `bur 7k3`, and `BUR7K3` all hit the same row.
- **Cancelled reservations are findable.** `status` is just a field; the code remains a valid lookup key for history.
- **No enumeration leak**: unknown, malformed, and "matches but cancelled" all share the same response shape (`200` for matches regardless of status, `404` otherwise).

### Existing endpoints (no behavior change, only shape)

- `GET /reservations/{id}` — response now includes `confirmation_code`.
- `GET /reservations` (list) — each row in the array now includes `confirmation_code`.
- `PATCH /reservations/{id}` — code does **not** change on edit. Attempting to PATCH `confirmation_code` returns 422.
- `POST /reservations/{id}/cancel` — code is preserved through cancellation.

## Lifecycle guarantees

| Event | Effect on `confirmation_code` |
|---|---|
| `POST /reservations` | new unique code minted and returned |
| `PATCH /reservations/{id}` (any field) | unchanged |
| `POST /reservations/{id}/cancel` | unchanged |
| Server restart with pre-existing rows missing a code | lazy backfill assigns one on next `init_db()` |
| Code reuse | never — the partial UNIQUE index prevents it, even if the same string is later regenerated |

## Migration & backfill

- Idempotent migration in `_migrate()` adds the column (nullable) plus the UNIQUE index.
- `_backfill_confirmation_codes()` runs on every `init_db()`. The first call assigns codes to all existing rows; subsequent calls early-return when no NULLs exist (cheap probe).
- The live DB has been backfilled. **All 205 rows have unique codes; 0 NULL.**

## What this does NOT do (out of scope, future work)

This change is purely the **data layer**. Specifically out of scope:

- ❌ Phone-OTP / SMS / WhatsApp delivery
- ❌ Code-required for mutations
- ❌ Customer-facing email/SMS that surfaces the code on booking
- ❌ Rate limiting on `/reservations/by-code/*` beyond the global per-cid limit already in place
- ❌ Admin tools to regenerate or invalidate codes

## How to use this from another consumer (e.g. agent app)

When another LLM or service is asked to wire up code support:

1. **Always fetch the code** from the API response when creating a reservation; store it in conversation state. Don't try to compute or guess it.
2. **Quote the code back to users** in confirmation messages: *"Andrés, your reservation for May 25 is confirmed. Reference: BUR-7K3."*
3. **Accept code as an alternative identifier** in user input. If the user says "I want to change BUR7K3," look it up via `GET /reservations/by-code/BUR7K3` first, then PATCH the resulting `id`.
4. **Treat the code as low-security**. It's safe to print, screenshot, repeat aloud. Never base authorization decisions on possession of the code alone.
5. **For mutations, still require the existing checks**: agent JWT + phone match in scheduler-tool guardrails.

## Suggested follow-up prompt for an agent integration task

> Update the scheduler agent to use confirmation codes. Whenever the agent quotes a reservation to the user, also include the code formatted as `XXX-XXX`. When parsing user input that contains a 6-character alphanumeric token (case-insensitive, with optional dash/space), call `GET /reservations/by-code/{normalized}` before falling back to phone-based listing. Codes are low-security identifiers — do not treat them as authentication; the existing phone-match guardrail on writes still applies.

## Verification

```bash
uv run pytest -q                          # 93/93 passing (81 prior + 12 new)
sqlite3 data/reservations.db ".schema reservations"
sqlite3 data/reservations.db "SELECT COUNT(*), COUNT(confirmation_code), COUNT(DISTINCT confirmation_code) FROM reservations"
# → 205, 205, 205

# live API
TOKEN=$(curl -sS -X POST localhost:8765/auth/token \
  -H 'content-type: application/json' \
  -d "{\"client_id\":\"$CLIENT_ID\",\"client_secret\":\"$CLIENT_SECRET\"}" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -H "authorization: Bearer $TOKEN" localhost:8765/reservations/128 | jq .confirmation_code
curl -H "authorization: Bearer $TOKEN" localhost:8765/reservations/by-code/<code-from-above> | jq .id
```
