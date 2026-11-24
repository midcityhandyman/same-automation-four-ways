"""Validation, normalization and scheduling rules (spec steps S2 and S7).

n8n/normalize_lead.js mirrors this module line for line. A test runs both on
the same payloads when Node.js is available.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .config import Config

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ValidationError(ValueError):
    """The payload cannot become a lead."""


@dataclass(frozen=True)
class Lead:
    key: str
    name: str
    email: str | None
    phone: str | None
    service: str
    message: str
    source: str
    received_at: datetime


def normalize_phone(value: Any) -> str | None:
    text = str(value or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if text.startswith("+"):  # already international: never guess a country code
        return "+" + digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def normalize_email(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if EMAIL_RE.match(text) else None


def parse_time(value: Any, default: datetime) -> datetime:
    if not value:
        return default
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_lead(payload: Mapping[str, Any], now: datetime) -> Lead:
    if not isinstance(payload, Mapping):
        raise ValidationError("payload is not an object")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValidationError("missing name")
    phone = normalize_phone(payload.get("phone"))
    email = normalize_email(payload.get("email"))
    if not phone and not email:
        raise ValidationError("no usable phone or email")
    try:
        received_at = parse_time(payload.get("received_at"), now)
    except ValueError as exc:
        raise ValidationError("received_at is not an ISO timestamp") from exc
    return Lead(
        key=f"phone:{phone}" if phone else f"email:{email}",
        name=name,
        email=email,
        phone=phone,
        service=str(payload.get("service") or "general").strip() or "general",
        message=str(payload.get("message") or "").strip(),
        source=str(payload.get("source") or "unknown").strip() or "unknown",
        received_at=received_at,
    )


def quiet_adjust(moment: datetime, config: Config) -> datetime:
    """Move a send time into the local send window. Inside the window: unchanged."""
    offset = timedelta(hours=config.utc_offset_hours)
    local = moment + offset
    if local.hour < config.send_window_start:
        pass
    elif local.hour >= config.send_window_end:
        local = local + timedelta(days=1)
    else:
        return moment
    local = local.replace(hour=config.send_window_start, minute=0, second=0, microsecond=0)
    return local - offset


def plan_schedule(received_at: datetime, config: Config) -> list[tuple[str, datetime]]:
    """Return (step, due time) pairs: the follow ups, then the close step."""
    plan = [
        (f"follow_up_{index}", quiet_adjust(received_at + timedelta(days=days), config))
        for index, days in enumerate(config.follow_up_days, start=1)
    ]
    plan.append(("close", quiet_adjust(received_at + timedelta(days=config.close_after_days), config)))
    return plan
