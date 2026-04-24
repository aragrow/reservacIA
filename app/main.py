from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.routers import auth, reservations, tables
from app.security import IPAllowlistMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="reservacIA",
    description="Restaurant reservation API for a single AI agent consumer.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(IPAllowlistMiddleware)

app.include_router(auth.router)
app.include_router(tables.router)
app.include_router(reservations.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
