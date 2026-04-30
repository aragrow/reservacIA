from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings


def _ensure_aware(dt: datetime) -> datetime:
    """Naive datetimes are interpreted as wall-clock in the restaurant's
    configured timezone (Europe/Madrid by default). Aware datetimes pass
    through untouched. This is the single point at the API boundary that
    eliminates the naive/aware mismatch downstream in conflict-detection."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(get_settings().timezone))
    return dt

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
    expires_at: str                 # human-readable, formatted in the restaurant's locale (Madrid)
    refresh_token: str
    refresh_expires_in: int         # refresh token lifetime in seconds (~6 months)
    refresh_expires_at: str         # human-readable Madrid-format expiry for the refresh token


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

    @field_validator("reservation_at")
    @classmethod
    def _normalize_tz(cls, v: datetime) -> datetime:
        return _ensure_aware(v)


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

    @field_validator("reservation_at")
    @classmethod
    def _normalize_tz(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _ensure_aware(v) if v is not None else v


class ReservationOut(BaseModel):
    # Deliberately does NOT inherit ReservationBase: phone and customer_name
    # are PII and must not leak through reads. Agents identify a booking by
    # `id` or `confirmation_code` only.
    id: int
    status: ReservationStatus
    party_size: int
    reservation_at: datetime
    notes: Optional[str] = None
    table_id: Optional[int] = None
    table: Optional[TableOut] = None
    confirmation_code: str
    created_at: datetime
    updated_at: datetime


# --- reviews -----------------------------------------------------------------

ReviewAuthorRole = Literal["restaurant", "customer"]


class ReviewCommentCreate(BaseModel):
    author_role: ReviewAuthorRole
    author_name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=2000)


class ReviewCommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: Optional[str] = Field(default=None, min_length=1, max_length=2000)


class ReviewCommentOut(BaseModel):
    # `author_name` omitted — comment authors are clients (or staff) and we
    # don't expose names through reads. `author_role` keeps the coarse
    # restaurant-vs-customer distinction.
    id: int
    review_id: int
    author_role: ReviewAuthorRole
    body: str
    created_at: datetime
    updated_at: datetime


class ReviewCreate(BaseModel):
    reviewer_name: str = Field(min_length=1, max_length=200)
    reviewer_city: Optional[str] = Field(default=None, max_length=100)
    rating: int = Field(ge=1, le=5)
    body: str = Field(min_length=1, max_length=5000)


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    reviewer_city: Optional[str] = Field(default=None, max_length=100)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    body: Optional[str] = Field(default=None, min_length=1, max_length=5000)


class ReviewOut(BaseModel):
    # `reviewer_name` and `reviewer_city` omitted — both are client-identifying.
    # Reviews are still discoverable by id, rating, and body content.
    id: int
    rating: int
    body: str
    comments: list[ReviewCommentOut] = []
    created_at: datetime
    updated_at: datetime


# --- notifications ----------------------------------------------------------

NotificationKind = Literal[
    "created", "updated", "cancelled", "reminder", "custom"
]
NotificationStatus = Literal[
    "pending", "in_flight", "sent", "failed", "cancelled"
]


class NotificationOut(BaseModel):
    # `phone` is intentionally omitted — it's the recipient and is PII. The
    # rendered `body` is safe: templates substitute date/time/party/room/code
    # only, never the customer's name.
    id: int
    reservation_id: Optional[int] = None
    kind: NotificationKind
    scheduled_at: datetime
    status: NotificationStatus
    attempts: int
    last_error: Optional[str] = None
    body: str
    sent_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class NotificationCreate(BaseModel):
    """Body for `POST /notifications` — agent-driven custom message.

    The endpoint hardcodes `kind='custom'`; agents cannot impersonate the
    reservation lifecycle events (`created` / `updated` / `cancelled` /
    `reminder`) which are reserved for CRUD-driven hooks.
    """
    phone: str = Field(min_length=_PHONE_MIN, max_length=_PHONE_MAX)
    body: str = Field(min_length=1, max_length=2000)
    reservation_id: Optional[int] = Field(default=None, ge=1)
    scheduled_at: Optional[datetime] = None  # default: now (immediate dispatch)

    @field_validator("phone")
    @classmethod
    def _phone_digits(cls, v: str) -> str:
        cleaned = v.strip()
        stripped = cleaned.lstrip("+")
        if not stripped.isdigit():
            raise ValueError("phone must contain only digits, optionally prefixed with '+'")
        return cleaned
