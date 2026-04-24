from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app import crud
from app.db import connection
from app.models import (
    ReviewCommentCreate,
    ReviewCommentOut,
    ReviewCommentUpdate,
    ReviewCreate,
    ReviewOut,
    ReviewUpdate,
)
from app.security import require_agent

router = APIRouter(
    prefix="/reviews",
    tags=["reviews"],
    dependencies=[Depends(require_agent)],
)


# --- reviews ------------------------------------------------------------------

@router.get("", response_model=list[ReviewOut])
def list_reviews(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    min_rating: Optional[int] = Query(default=None, ge=1, le=5),
) -> list[dict]:
    with connection() as conn:
        return [
            dict(row, comments=crud.list_review_comments(conn, row["id"]))
            for row in crud.list_reviews(
                conn, limit=limit, offset=offset, min_rating=min_rating
            )
        ]


@router.post("", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def create(body: ReviewCreate) -> dict:
    with connection() as conn:
        return crud.create_review(conn, body)


@router.get("/{review_id}", response_model=ReviewOut)
def get_one(review_id: int) -> dict:
    with connection() as conn:
        row = crud.get_review(conn, review_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review not found")
    return row


@router.patch("/{review_id}", response_model=ReviewOut)
def update(review_id: int, body: ReviewUpdate) -> dict:
    with connection() as conn:
        updated = crud.update_review(conn, review_id, body)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review not found")
    return updated


# --- comments (nested) --------------------------------------------------------

@router.get("/{review_id}/comments", response_model=list[ReviewCommentOut])
def list_comments(review_id: int) -> list[dict]:
    with connection() as conn:
        if crud.get_review(conn, review_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review not found")
        return crud.list_review_comments(conn, review_id)


@router.post(
    "/{review_id}/comments",
    response_model=ReviewCommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(review_id: int, body: ReviewCommentCreate) -> dict:
    with connection() as conn:
        if crud.get_review(conn, review_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review not found")
        return crud.create_review_comment(conn, review_id, body)


@router.patch(
    "/{review_id}/comments/{comment_id}",
    response_model=ReviewCommentOut,
)
def update_comment(
    review_id: int, comment_id: int, body: ReviewCommentUpdate
) -> dict:
    with connection() as conn:
        existing = crud.get_review_comment(conn, comment_id)
        if existing is None or existing["review_id"] != review_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="comment not found")
        updated = crud.update_review_comment(conn, comment_id, body)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="comment not found")
    return updated
