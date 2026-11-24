"""Runtime settings. Every value has a safe default so nothing needs a key."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Config:
    utc_offset_hours: float = -5.0  # local time = UTC + offset (no DST handling)
    send_window_start: int = 8  # first local hour a follow up may be sent
    send_window_end: int = 20  # first local hour that is too late
    dedupe_window_days: int = 30
    max_attempts: int = 3
    backoff_base_seconds: float = 2.0
    follow_up_days: tuple[int, ...] = (1, 3, 7)
    close_after_days: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        base = cls()
        return cls(
            utc_offset_hours=float(env.get("LOCAL_UTC_OFFSET_HOURS", base.utc_offset_hours)),
            send_window_start=int(env.get("SEND_WINDOW_START", base.send_window_start)),
            send_window_end=int(env.get("SEND_WINDOW_END", base.send_window_end)),
            dedupe_window_days=int(env.get("DEDUPE_WINDOW_DAYS", base.dedupe_window_days)),
            max_attempts=int(env.get("MAX_ATTEMPTS", base.max_attempts)),
            backoff_base_seconds=float(env.get("BACKOFF_BASE_SECONDS", base.backoff_base_seconds)),
        )
