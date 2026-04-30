from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import crud
from app.crud import DomainError
from app.db import connection
from app.models import (
    ReservationOut,
    ReservationStatus,
    TableCreate,
    TableOut,
    _ensure_aware,
    TableUpdate,
)
from app.security import require_agent

router = APIRouter(
    prefix="/tables",
    tags=["tables"],
    dependencies=[Depends(require_agent)],
)


@router.get("", response_model=list[TableOut])
def list_all(
    room_id: Optional[int] = Query(default=None, ge=1),
) -> list[dict]:
    with connection() as conn:
        return crud.list_tables(conn, room_id=room_id)


@router.post("", response_model=TableOut, status_code=status.HTTP_201_CREATED)
def create(body: TableCreate) -> dict:
    try:
        with connection() as conn:
            return crud.create_table(conn, body)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/available", response_model=list[TableOut])
def list_available(
    at: datetime = Query(
        ...,
        description="Target reservation time (ISO 8601, e.g. 2026-05-01T19:00:00Z).",
    ),
    party_size: Optional[int] = Query(
        default=None,
        ge=1,
        le=200,
        description="Only include tables with capacity >= party_size.",
    ),
    room_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="Only include tables in this room.",
    ),
) -> list[dict]:
    """Tables with no confirmed reservation within 2 hours of `at`.

    Ordered smallest-capacity-first so the first element is the best fit.

    Naive `at` (no `Z` / `+HH:MM` suffix) is interpreted as the restaurant's
    local timezone — same convention the Pydantic validators use for
    `reservation_at` on POST/PATCH /reservations. Without this, the
    aware-vs-naive subtraction in the conflict-window check raised
    TypeError → 500.
    """
    at = _ensure_aware(at)
    with connection() as conn:
        return crud.find_all_available_tables(
            conn, at=at, party_size=party_size, room_id=room_id
        )


@router.get("/{table_id}", response_model=TableOut)
def get_one(table_id: int) -> dict:
    with connection() as conn:
        row = crud.get_table(conn, table_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")
    return row


@router.patch("/{table_id}", response_model=TableOut)
def update(table_id: int, body: TableUpdate) -> dict:
    try:
        with connection() as conn:
            updated = crud.update_table(conn, table_id, body)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")
    return updated


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(table_id: int) -> None:
    try:
        with connection() as conn:
            deleted = crud.delete_table(conn, table_id)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")


@router.get("/{table_id}/reservations", response_model=list[ReservationOut])
def list_reservations_for_table(
    table_id: int,
    status_: Optional[ReservationStatus] = Query(default=None, alias="status"),
) -> list[dict]:
    with connection() as conn:
        if crud.get_table(conn, table_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")
        return crud.list_reservations(conn, table_id=table_id, status=status_)
