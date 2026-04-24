from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import crud
from app.crud import ReservationError
from app.db import connection
from app.models import ReservationCreate, ReservationOut, ReservationStatus, ReservationUpdate
from app.security import require_agent

router = APIRouter(
    prefix="/reservations",
    tags=["reservations"],
    dependencies=[Depends(require_agent)],
)


@router.get("", response_model=list[ReservationOut])
def list_reservations(
    phone: Optional[str] = Query(default=None),
    status_: Optional[ReservationStatus] = Query(default=None, alias="status"),
    table_id: Optional[int] = Query(default=None, ge=1),
) -> list[dict]:
    with connection() as conn:
        return crud.list_reservations(conn, phone=phone, status=status_, table_id=table_id)


@router.get("/{reservation_id}", response_model=ReservationOut)
def get_one(reservation_id: int) -> dict:
    with connection() as conn:
        row = crud.get_reservation(conn, reservation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reservation not found")
    return row


@router.post("", response_model=ReservationOut, status_code=status.HTTP_201_CREATED)
def create(body: ReservationCreate) -> dict:
    try:
        with connection() as conn:
            return crud.create_reservation(conn, body)
    except ReservationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.patch("/{reservation_id}", response_model=ReservationOut)
def update(reservation_id: int, body: ReservationUpdate) -> dict:
    try:
        with connection() as conn:
            existing = crud.get_reservation(conn, reservation_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reservation not found")
            updated = crud.update_reservation(conn, reservation_id, body)
    except ReservationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reservation not found")
    return updated


@router.post("/{reservation_id}/cancel", response_model=ReservationOut)
def cancel(reservation_id: int) -> dict:
    with connection() as conn:
        row = crud.cancel_reservation(conn, reservation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reservation not found")
    return row
