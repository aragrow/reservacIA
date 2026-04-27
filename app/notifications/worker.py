"""In-process notification worker.

Polls the `notifications` queue at a configurable interval, claims due rows,
and dispatches each through the configured `Notifier`. Started from
FastAPI's `lifespan` and cancelled on shutdown.

Single instance / single tenant: no leader election or Redis. The atomic
status-flip in `queue.pick_due` makes it safe even if two workers ever ran
side-by-side, but in this deployment it's strictly single-writer.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import Settings, get_settings
from app.db import connection
from app.notifications import queue
from app.notifications.notifier import Notifier, build_notifier

_log = logging.getLogger("reservacIA.notifications.worker")


async def run_forever(
    notifier: Notifier | None = None, settings: Settings | None = None
) -> None:
    """Async coroutine: poll → process → sleep, forever, until cancelled.

    Crashes inside one tick are logged and swallowed so the loop self-heals
    and a single bad message can't stall the whole queue.
    """
    settings = settings or get_settings()
    notifier = notifier or build_notifier(settings)
    interval = settings.notification_worker_interval_seconds
    _log.info(
        "notification worker starting (interval=%ss, suppressed=%s)",
        interval, settings.suppress_notifications,
    )
    try:
        while True:
            try:
                process_batch(notifier, settings)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("notification worker tick failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        _log.info("notification worker shutting down")
        raise


def process_batch(notifier: Notifier, settings: Settings) -> int:
    """One worker tick: claim due rows in a short transaction, then dispatch
    them outside the transaction so a slow provider doesn't hold a write
    lock. Returns the number of rows successfully sent."""
    only_kind = "custom" if settings.suppress_notifications else None
    with connection() as conn:
        due = queue.pick_due(conn, limit=20, only_kind=only_kind)

    sent = 0
    for row in due:
        try:
            notifier.send(phone=row["phone"], body=row["body"])
        except Exception as exc:  # noqa: BLE001 — provider errors are opaque
            _log.warning(
                "notification %s failed (attempt %s): %s",
                row["id"], row["attempts"], exc,
            )
            with connection() as conn:
                queue.mark_failed_or_retry(
                    conn, row["id"], str(exc),
                    max_attempts=settings.notification_max_attempts,
                )
            continue
        with connection() as conn:
            queue.mark_sent(conn, row["id"])
        sent += 1
    return sent
