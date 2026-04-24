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

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | IP only | liveness |
| POST | `/auth/token` | IP only | exchange `client_id`+`client_secret` for an access+refresh token pair |
| POST | `/auth/refresh` | IP only | exchange a refresh token for a new access+refresh pair (rotating) |
| GET | `/tables` | JWT | list all 50 tables |
| GET | `/tables/{id}` | JWT | fetch one table |
| GET | `/tables/{id}/reservations` | JWT | reservations assigned to a given table (supports `?status=...`) |
| GET | `/reservations` | JWT | list; filters: `?phone=...&status=confirmed\|cancelled&table_id=...` |
| GET | `/reservations/{id}` | JWT | fetch one |
| POST | `/reservations` | JWT | create (auto-assigns smallest fitting table; optional `table_id` pins) |
| PATCH | `/reservations/{id}` | JWT | partial update |
| POST | `/reservations/{id}/cancel` | JWT | soft-cancel (sets `status='cancelled'`) |

Interactive OpenAPI docs: `http://localhost:8765/docs` — click **Authorize** 🔒, paste the access token into the `HTTPBearer` field, and every protected call uses it automatically.

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

Covers: token issuance, every auth failure mode, IP allowlist (allowed + blocked), reservation lifecycle, validation, 404s.

## Data model

```
tables (
    id            INTEGER PK,
    table_number  TEXT UNIQUE,           -- e.g. 'T01'
    capacity      INTEGER CHECK (BETWEEN 2 AND 12),
    created_at
)
-- 50 tables seeded: 16×2 + 16×4 + 10×6 + 5×8 + 2×10 + 1×12

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

## Seed tables + backfill

```bash
uv run python scripts/seed_tables.py   # idempotent: creates 50 tables, backfills any unassigned confirmed reservations
```

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
    ├── auth.py          # POST /auth/token
    └── reservations.py  # CRUD + query endpoints
scripts/
├── seed.py              # 100 past + 100 future reservations across ~95 customers
└── seed_tables.py       # 50 tables + backfill confirmed reservations
tests/                   # pytest suite (conftest pins TestClient IP to 127.0.0.1)
```

## Not included (by design)

- The AI agent itself (built separately)
- Rate limiting (single trusted consumer)
- Migrations tooling (single-table schema; init-on-startup is sufficient)
- HTTPS termination (handled by the deployment layer)
- Multi-tenancy
