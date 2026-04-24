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
    local_mode: bool = False                # dev-only: enable /_debug/dev-token and auto-auth /docs
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
