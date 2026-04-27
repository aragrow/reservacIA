from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import crud
from app.config import get_settings
from app.db import connection
from app.models import (
    NotificationCreate,
    NotificationKind,
    NotificationOut,
    NotificationStatus,
)
from app.notifications import queue
from app.security import require_agent

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_agent)],
)


@router.post(
    "", response_model=NotificationOut, status_code=status.HTTP_201_CREATED
)
def create(body: NotificationCreate) -> dict:
    """Enqueue an agent-driven message. Always stored with `kind='custom'`.

    `scheduled_at` defaults to "now" — the worker picks it up on the next
    tick. To schedule a future send, pass an ISO 8601 datetime; naive
    timestamps are interpreted as the restaurant's local timezone (same
    convention as `reservation_at`).
    """
    if body.reservation_id is not None:
        with connection() as conn:
            if crud.get_reservation(conn, body.reservation_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"reservation {body.reservation_id} does not exist",
                )

    when = body.scheduled_at
    if when is None:
        when = datetime.now(tz=ZoneInfo(get_settings().timezone))
    elif when.tzinfo is None:
        when = when.replace(tzinfo=ZoneInfo(get_settings().timezone))

    with connection() as conn:
        new_id = queue.enqueue(
            conn,
            reservation_id=body.reservation_id,
            kind="custom",
            phone=body.phone,
            scheduled_at=when,
            body=body.body,
        )
        # `custom` is exempt from SUPPRESS_NOTIFICATIONS, so enqueue always
        # returns an id here.
        assert new_id is not None
        row = queue.get_notification(conn, new_id)
    assert row is not None
    return row


@router.get("", response_model=list[NotificationOut])
def list_all(
    phone: Optional[str] = Query(
        default=None,
        description="Exact phone match. URL-encode '+' as %2B (or use --data-urlencode in curl).",
    ),
    reservation_id: Optional[int] = Query(default=None, ge=1),
    status_: Optional[NotificationStatus] = Query(default=None, alias="status"),
    kind: Optional[NotificationKind] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """List queued or sent notifications, newest first.

    All filters are optional and combinable. With no filters this returns the
    most recent `limit` rows across the whole queue — useful as an audit pane.
    """
    with connection() as conn:
        return queue.list_notifications(
            conn,
            phone=phone,
            reservation_id=reservation_id,
            status=status_,
            kind=kind,
            limit=limit,
            offset=offset,
        )


@router.get("/{notification_id}", response_model=NotificationOut)
def get_one(notification_id: int) -> dict:
    with connection() as conn:
        row = queue.get_notification(conn, notification_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="notification not found"
        )
    return row
