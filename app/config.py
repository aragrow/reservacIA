from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_path: str = Field(default="./data/reservations.db")
    jwt_secret: str
    jwt_ttl_minutes: int = 60 * 24          # 24h access token
    refresh_ttl_days: int = 180             # 6 months refresh window; rotates on each use
    client_id: str
    client_secret: str
    allowed_ips: str = "127.0.0.1/32"

    # Localization — defaults assume the restaurant is in Spain. Override per deployment.
    locale: str = "es_ES"
    timezone: str = "Europe/Madrid"         # IANA tz name; handles CET/CEST DST
    date_format: str = "%d/%m/%Y"           # e.g. 25/04/2026
    time_format: str = "%H:%M"              # 24-hour, e.g. 19:30
    datetime_format: str = "%d/%m/%Y %H:%M" # e.g. 25/04/2026 19:30

    local_mode: bool = False                # dev-only: enable /_debug/dev-token and auto-auth /docs

    # Security knobs (see docs in /app/middleware.py).
    audit_log_path: str = "./data/audit.jsonl"
    rate_limit_data_per_minute: int = 60        # per-cid limit on /reservations,/rooms,/tables,/reviews
    rate_limit_auth_per_minute: int = 5         # per-IP limit on /auth/token (brute-force throttle)
    rate_limit_other_per_minute: int = 30       # per-cid limit on /auth/refresh and /_debug/*
    max_body_bytes: int = 64 * 1024             # reject bodies larger than this (413)
    # Pre-issued dev tokens kept in .env purely for convenience. Only surfaced
    # by /docs examples when local_mode=true. Optional — /_debug/dev-token
    # issues fresh ones on demand.
    access_token: str | None = None
    refresh_token: str | None = None

    @field_validator("allowed_ips")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ALLOWED_IPS must not be empty")
        return v

    def allowed_networks(self) -> list[IPv4Network | IPv6Network]:
        return [ip_network(part.strip(), strict=False) for part in self.allowed_ips.split(",") if part.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
