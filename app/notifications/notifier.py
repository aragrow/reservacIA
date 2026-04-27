"""Notifier protocol + concrete impls.

The protocol exists so a real provider (Twilio, WhatsApp Business, etc.) can
slot in later with zero changes elsewhere. v1 ships ConsoleNotifier (logs
only, no external calls) and MockNotifier (capturing — used by tests).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings


class Notifier(Protocol):
    """Sends a single transactional message. Synchronous on purpose — the
    worker handles async/concurrency above this layer."""
    def send(self, *, phone: str, body: str) -> None: ...


class ConsoleNotifier:
    """v1 default: log the message and append a structured row to
    `data/audit.jsonl` (the same file the security middleware writes to)
    so all forensic data lives in one place."""

    def __init__(self, audit_log_path: str) -> None:
        self._log = logging.getLogger("reservacIA.notifications")
        self._audit_path = Path(audit_log_path)

    def send(self, *, phone: str, body: str) -> None:
        # Mask the middle of the phone in logs; full number is in the queue
        # row already, no need to repeat it in human-readable logs.
        masked = _mask_phone(phone)
        self._log.info("notification dispatched to %s: %s", masked, body)

        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "event": "notification_sent",
                "channel": "console",
                "phone_masked": masked,
                "body_chars": len(body),
            }
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            # Audit failure must not propagate — same policy as the middleware.
            self._log.exception("audit append failed for notification")


class DisabledNotifier:
    """No-op. Useful in tests or to quickly silence the channel without
    reverting CRUD hooks."""

    def send(self, *, phone: str, body: str) -> None:  # pragma: no cover
        return


def build_notifier(settings: Settings | None = None) -> Notifier:
    s = settings or get_settings()
    if s.notifier == "console":
        return ConsoleNotifier(audit_log_path=s.audit_log_path)
    if s.notifier == "disabled":
        return DisabledNotifier()
    raise ValueError(
        f"unsupported notifier: {s.notifier!r} "
        "(supported in v1: 'console', 'disabled')"
    )


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return "***"
    return phone[:3] + "****" + phone[-4:]
