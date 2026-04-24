from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import crud
from app.db import connection
from app.models import ReservationOut, ReservationStatus, TableOut
from app.security import require_agent

router = APIRouter(
    prefix="/tables",
    tags=["tables"],
    dependencies=[Depends(require_agent)],
)


@router.get("", response_model=list[TableOut])
def list_all() -> list[dict]:
    with connection() as conn:
        return crud.list_tables(conn)


@router.get("/{table_id}", response_model=TableOut)
def get_one(table_id: int) -> dict:
    with connection() as conn:
        row = crud.get_table(conn, table_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")
    return row


@router.get("/{table_id}/reservations", response_model=list[ReservationOut])
def list_reservations_for_table(
    table_id: int,
    status_: Optional[ReservationStatus] = Query(default=None, alias="status"),
) -> list[dict]:
    with connection() as conn:
        if crud.get_table(conn, table_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="table not found")
        return crud.list_reservations(conn, table_id=table_id, status=status_)
