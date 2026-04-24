from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app import crud
from app.crud import DomainError
from app.db import connection
from app.models import RoomCreate, RoomOut, RoomUpdate, TableOut
from app.security import require_agent

router = APIRouter(
    prefix="/rooms",
    tags=["rooms"],
    dependencies=[Depends(require_agent)],
)


@router.get("", response_model=list[RoomOut])
def list_all() -> list[dict]:
    with connection() as conn:
        return crud.list_rooms(conn)


@router.post("", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
def create(body: RoomCreate) -> dict:
    try:
        with connection() as conn:
            return crud.create_room(conn, body)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/{room_id}", response_model=RoomOut)
def get_one(room_id: int) -> dict:
    with connection() as conn:
        row = crud.get_room(conn, room_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
    return row


@router.patch("/{room_id}", response_model=RoomOut)
def update(room_id: int, body: RoomUpdate) -> dict:
    try:
        with connection() as conn:
            updated = crud.update_room(conn, room_id, body)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
    return updated


@router.delete("/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(room_id: int) -> None:
    try:
        with connection() as conn:
            deleted = crud.delete_room(conn, room_id)
    except DomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")


@router.get("/{room_id}/tables", response_model=list[TableOut])
def list_tables_in_room(room_id: int) -> list[dict]:
    with connection() as conn:
        if crud.get_room(conn, room_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="room not found")
        return crud.list_tables(conn, room_id=room_id)
