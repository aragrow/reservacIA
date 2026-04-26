# reservacIA

A single-tenant Restaurant reservation API designed to be consumed by one trusted AI Agent. Backend only — no frontend. Built on FastAPI + SQLite.

## Security model (two layers)

Every protected request passes through both:

1. **IP allowlist** (middleware) — the source IP must match a CIDR in `ALLOWED_IPS`, otherwise `403`.
2. **Bearer JWT** — `Authorization: Bearer <jwt>` signed with `JWT_SECRET` (HS256). The token carries a `cid` claim which must match the configured `CLIENT_ID`, plus a `typ` claim that distinguishes access tokens (24h) from refresh tokens (180d). Identity is fully established from the signed token alone — no separate client-id header.

## Setup (uv)

```bash
uv sync                        # creates .venv + installs deps from pyproject.toml / uv.lock
cp .env.example .env           # then edit secrets / ALLOWED_IPS
./run.sh                        # starts uvicorn in the background on :8765
```

Override host/port with env vars: `RESERVACIA_HOST=127.0.0.1 RESERVACIA_PORT=8765 ./run.sh`.

`./run.sh` subcommands: `start` (default) | `stop` | `restart` | `status` | `logs`. Starting is idempotent — it stops any previous instance (tracked via `data/run.pid`) before launching a fresh one.

`.env` is loaded automatically by `pydantic-settings`. Required keys:

| key | purpose |
|---|---|
| `DATABASE_PATH` | path to the SQLite file (parent dir is created on startup) |
| `JWT_SECRET` | HS256 signing secret (use `openssl rand -hex 32`) |
| `JWT_TTL_MINUTES` | access token lifetime (default `1440` = 24h) |
| `REFRESH_TTL_DAYS` | refresh token lifetime (default `180` ≈ 6 months; rotates on use) |
| `CLIENT_ID` / `CLIENT_SECRET` | the agent's credentials for `/auth/token` |
| `ALLOWED_IPS` | comma-separated CIDR list, e.g. `127.0.0.1/32,10.0.0.0/24` |
| `LOCAL_MODE` | when `true`, enables dev-only conveniences (see below). MUST be `false` in production. |
| `ACCESS_TOKEN` / `REFRESH_TOKEN` | optional — pre-issued dev tokens surfaced as `/docs` examples in local mode |

### Local dev mode (`LOCAL_MODE=true`)

Three conveniences, all loopback-guarded:
- **`GET /_debug/dev-token`** — returns a fresh `access_token` + `refresh_token` pair without requiring credentials. 404 outside local mode, 403 off-loopback. Not in the OpenAPI spec.
- **`/docs` auto-authorizes on load** — the custom docs page calls `/_debug/dev-token` and preauthorizes Swagger's `HTTPBearer` scheme. Just open the page and every protected route works. No copy-paste.
- **Pre-filled request-body examples** on `/auth/token` and `/auth/refresh` using your real `.env` values, so manual "Try it out" also works without typing.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | IP only | liveness |
| POST | `/auth/token` | IP only | exchange `client_id`+`client_secret` for an access+refresh token pair |
| POST | `/auth/refresh` | IP only | exchange a refresh token for a new access+refresh pair (rotating) |
| GET | `/rooms` | JWT | list rooms (Bar, Booths, Dining Room 1, Dining Room 2, plus any you create) |
| POST | `/rooms` | JWT | create a room |
| GET | `/rooms/{id}` | JWT | fetch one room |
| PATCH | `/rooms/{id}` | JWT | update name/description |
| DELETE | `/rooms/{id}` | JWT | delete (409 if any table still assigned) |
| GET | `/rooms/{id}/tables` | JWT | all tables in a given room |
| GET | `/tables` | JWT | list tables; optional `?room_id=N` filter |
| POST | `/tables` | JWT | create a table (`table_number`, `capacity 2-12`, optional `room_id`) |
| GET | `/tables/available` | JWT | tables with no conflict within 2h of `?at=ISO`; filters: `?party_size=N&room_id=N` |
| GET | `/tables/{id}` | JWT | fetch one table (includes nested `room`) |
| PATCH | `/tables/{id}` | JWT | update number/capacity/room (409 if shrinking capacity below an existing party) |
| DELETE | `/tables/{id}` | JWT | delete (409 if referenced by any reservation) |
| GET | `/tables/{id}/reservations` | JWT | reservations assigned to a given table (supports `?status=...`) |
| GET | `/reservations` | JWT | list; filters: `?phone=...&status=confirmed\|cancelled&table_id=...` |
| GET | `/reservations/{id}` | JWT | fetch one |
| POST | `/reservations` | JWT | create (auto-assigns smallest fitting table; optional `table_id` pins) |
| PATCH | `/reservations/{id}` | JWT | partial update |
| POST | `/reservations/{id}/cancel` | JWT | soft-cancel (sets `status='cancelled'`) |

Interactive OpenAPI docs: `http://localhost:8765/docs`. In local mode (`LOCAL_MODE=true`) the Authorize dialog is pre-filled automatically on page load — just open and go. In production mode, click **Authorize** 🔒 and paste an access token into the `HTTPBearer` field.

## Timezone handling

`reservation_at` accepts ISO 8601 with **or without** a timezone offset.
Naive timestamps (e.g. `"2026-05-01T19:30:00"`) are interpreted as
**`TIMEZONE` from `.env`** — `Europe/Madrid` by default. Aware timestamps
(`+02:00`, `Z`, etc.) are preserved as-is. The 2-hour conflict rule and
all other datetime arithmetic operate on timezone-aware values, so naive
and aware inputs that represent the same wall-clock moment in Madrid
collide as expected.

## Quick usage

```bash
# 1. Get a token pair (if you don't already have ACCESS_TOKEN from .env)
TOKEN=$(curl -sS -X POST localhost:8765/auth/token \
  -H 'content-type: application/json' \
  -d "{\"client_id\":\"$CLIENT_ID\",\"client_secret\":\"$CLIENT_SECRET\"}" \
  | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# 2. Create a reservation (table is auto-assigned)
curl -X POST localhost:8765/reservations \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"phone":"+15551234567","customer_name":"Jane","party_size":4,"reservation_at":"2026-05-01T19:30:00Z"}'

# 3. List by phone (use --data-urlencode so '+' survives)
curl -sS -H "authorization: Bearer $TOKEN" \
  --get "localhost:8765/reservations" --data-urlencode "phone=+15551234567"

# 4. List by table
curl -sS -H "authorization: Bearer $TOKEN" \
  "localhost:8765/tables/17/reservations?status=confirmed"

# 5. Cancel
curl -X POST "localhost:8765/reservations/1/cancel" \
  -H "authorization: Bearer $TOKEN"

# 6. Refresh the token when expired (every 24h). Rotates refresh token too.
curl -sS -X POST localhost:8765/auth/refresh \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"$REFRESH_TOKEN\"}"
```

## Seed data

`scripts/seed.py` populates the DB with a realistic mix for development:

- 100 past reservations across 75 customers (some customers with multiple prior visits; ~10% cancelled)
- 100 future reservations across a mix of those customers plus ~20 new ones
- 30 customers have two future reservations exactly 14 days apart

```bash
uv run python scripts/seed.py
```

Not idempotent — clear `data/reservations.db` before re-running.

## Tests

```bash
uv run pytest -q
```

The `pytest` dependency lives in the `dev` group in `pyproject.toml` and is installed by `uv sync` by default.

**55 tests** across five files covering: JWT issuance + refresh rotation, every auth failure mode (expired / forged signature / wrong `cid` claim / refresh-used-as-access / access-used-as-refresh), IP allowlist (allowed + blocked), full reservation lifecycle, table assignment rules (capacity, 2h conflict, 2h boundary, simultaneous different tables, auto-reassignment on patch), tables CRUD + delete guards, rooms CRUD + delete guards, availability endpoint filters, nested routes, validation, 404s.

## Data model

```
rooms (
    id           INTEGER PK,
    name         TEXT UNIQUE,             -- e.g. 'Bar', 'Booths', 'Dining Room 1'
    description  TEXT?,
    created_at, updated_at
)
-- 4 rooms seeded: Bar, Booths, Dining Room 1, Dining Room 2

tables (
    id            INTEGER PK,
    table_number  TEXT UNIQUE,           -- e.g. 'T01'
    capacity      INTEGER CHECK (BETWEEN 2 AND 12),
    room_id       INTEGER REFERENCES rooms(id),
    created_at
)
-- 50 tables: 16×2 + 16×4 + 10×6 + 5×8 + 2×10 + 1×12
-- split 10 (Bar) + 12 (Booths) + 14 (Dining 1) + 14 (Dining 2)

reservations (
    id              INTEGER PK,
    phone           TEXT,                       -- indexed; not unique (a phone can have many)
    customer_name   TEXT,
    party_size      INTEGER CHECK (> 0),
    reservation_at  TEXT (ISO 8601),
    notes           TEXT?,
    status          'confirmed' | 'cancelled',  -- indexed
    table_id        INTEGER REFERENCES tables(id),   -- indexed
    created_at,
    updated_at
)
```

**Table assignment rules** (enforced by the API):
- capacity ≥ party size
- no two confirmed reservations on the same table within 2 hours
- auto-assignment picks the smallest fitting capacity, breaking ties by current load so bookings spread across the floor

## Seed tables + rooms + backfill

```bash
uv run python scripts/seed_tables.py   # 50 tables, backfills confirmed reservations with table assignments
uv run python scripts/seed_rooms.py    # 4 rooms, assigns every table to one
```

Both are idempotent — safe to re-run.

## Project layout

```
app/
├── main.py              # FastAPI app, middleware, router wiring, DB init on startup
├── config.py            # env-loaded settings
├── db.py                # sqlite connection + schema init
├── security.py          # IPAllowlistMiddleware, JWT issue/verify, require_agent dep
├── models.py            # Pydantic request/response schemas
├── crud.py              # SQL helpers
└── routers/
    ├── auth.py          # POST /auth/token, /auth/refresh
    ├── debug.py         # GET /_debug/dev-token (local mode only, loopback-guarded)
    ├── rooms.py         # CRUD + /rooms/{id}/tables
    ├── tables.py        # CRUD + /tables/available + /tables/{id}/reservations
    └── reservations.py  # CRUD + filters
scripts/
├── seed.py              # 100 past + 100 future reservations across ~95 customers
├── seed_tables.py       # 50 tables + backfill confirmed reservations
└── seed_rooms.py        # 4 rooms + assigns every table to a room
tests/                   # pytest suite (conftest pins TestClient IP to 127.0.0.1)
```

## Not included (by design)

- The AI agent itself (built separately)
- Rate limiting (single trusted consumer)
- Migrations tooling (single-table schema; init-on-startup is sufficient)
- HTTPS termination (handled by the deployment layer)
- Multi-tenancy
