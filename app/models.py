from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ReservationStatus = Literal["confirmed", "cancelled"]

_PHONE_MIN = 7
_PHONE_MAX = 20


# --- auth ---------------------------------------------------------------------

class TokenRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int                 # access token lifetime in seconds (24h)
    refresh_token: str
    refresh_expires_in: int         # refresh token lifetime in seconds (~6 months)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# --- rooms (locations) --------------------------------------------------------

class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)


class RoomOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


# --- tables -------------------------------------------------------------------

class TableCreate(BaseModel):
    table_number: str = Field(min_length=1, max_length=20)
    capacity: int = Field(ge=2, le=12)
    room_id: Optional[int] = None


class TableUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_number: Optional[str] = Field(default=None, min_length=1, max_length=20)
    capacity: Optional[int] = Field(default=None, ge=2, le=12)
    room_id: Optional[int] = None


class TableOut(BaseModel):
    id: int
    table_number: str
    capacity: int
    room_id: Optional[int] = None
    room: Optional[RoomOut] = None
    created_at: datetime


# --- reservations -------------------------------------------------------------

class ReservationBase(BaseModel):
    phone: str = Field(min_length=_PHONE_MIN, max_length=_PHONE_MAX)
    customer_name: str = Field(min_length=1, max_length=200)
    party_size: int = Field(ge=1, le=200)
    reservation_at: datetime
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("phone")
    @classmethod
    def _phone_digits(cls, v: str) -> str:
        cleaned = v.strip()
        stripped = cleaned.lstrip("+")
        if not stripped.isdigit():
            raise ValueError("phone must contain only digits, optionally prefixed with '+'")
        return cleaned


class ReservationCreate(ReservationBase):
    # If provided, the API will attempt to use this specific table (validated for
    # capacity and the 2-hour spacing rule). If omitted, the smallest suitable
    # table is auto-assigned.
    table_id: Optional[int] = None


class ReservationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: Optional[str] = Field(default=None, min_length=_PHONE_MIN, max_length=_PHONE_MAX)
    customer_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    party_size: Optional[int] = Field(default=None, ge=1, le=200)
    reservation_at: Optional[datetime] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    table_id: Optional[int] = None

    @field_validator("phone")
    @classmethod
    def _phone_digits(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        cleaned = v.strip()
        stripped = cleaned.lstrip("+")
        if not stripped.isdigit():
            raise ValueError("phone must contain only digits, optionally prefixed with '+'")
        return cleaned


class ReservationOut(ReservationBase):
    id: int
    status: ReservationStatus
    table_id: Optional[int] = None
    table: Optional[TableOut] = None
    created_at: datetime
    updated_at: datetime
