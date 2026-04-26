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


# Declared before /{reservation_id} so the literal-path route always wins. The
# integer typing on reservation_id would also reject "by-code" with 422, but
# explicit ordering is the safer pattern.
@router.get("/by-code/{code}", response_model=ReservationOut)
def get_by_code(code: str) -> dict:
    """Look up a reservation by its short confirmation code (PNR-style).

    Case-insensitive; accepts dashes/spaces, e.g. 'BUR-7K3', 'bur 7k3', 'BUR7K3'.
    Returns the same 404 shape regardless of whether the code is unknown,
    cancelled, or simply malformed — no enumeration leak.
    """
    with connection() as conn:
        row = crud.get_reservation_by_code(conn, code)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="reservation not found")
    return row


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
