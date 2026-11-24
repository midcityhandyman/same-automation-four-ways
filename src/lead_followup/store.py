"""SQLite state: leads, scheduled jobs, the event log and the dead letter queue.

All timestamps are stored as UTC ISO strings with second precision so that
string comparison in SQL matches time order.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .normalize import Lead

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    key TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT, phone TEXT,
    service TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    lead_key TEXT NOT NULL, step TEXT NOT NULL, due_at TEXT NOT NULL,
    status TEXT NOT NULL, PRIMARY KEY (lead_key, step)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, lead_key TEXT,
    kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dead_letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, stage TEXT NOT NULL,
    lead_key TEXT, reason TEXT NOT NULL, payload TEXT NOT NULL
);
"""


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # leads
    def get_lead(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM leads WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        lead = dict(row)
        lead["created_at"] = from_iso(lead["created_at"])
        return lead

    def find_by_contact(self, phone: str | None, email: str | None) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT key FROM leads WHERE (phone IS NOT NULL AND phone = ?) "
            "OR (email IS NOT NULL AND email = ?) ORDER BY created_at DESC LIMIT 1",
            (phone, email),
        ).fetchone()
        return self.get_lead(row["key"]) if row else None

    def save_lead(self, lead: Lead, status: str, created_at: datetime) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO leads VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lead.key, lead.name, lead.email, lead.phone, lead.service, status, iso(created_at)),
        )
        self.db.commit()

    def set_status(self, key: str, status: str) -> None:
        self.db.execute("UPDATE leads SET status = ? WHERE key = ?", (status, key))
        self.db.commit()

    # jobs
    def add_job(self, lead_key: str, step: str, due_at: datetime) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?, ?, ?, 'pending')", (lead_key, step, iso(due_at))
        )
        self.db.commit()

    def claim_due(self, now: datetime) -> list[tuple[str, str]]:
        """Mark every due pending job as running and return it.

        Claiming before sending means a crash mid send leaves the job in
        'running' for a person to review, instead of sending twice.
        """
        rows = self.db.execute(
            "SELECT lead_key, step FROM jobs WHERE status = 'pending' AND due_at <= ? "
            "ORDER BY due_at, lead_key, step",
            (iso(now),),
        ).fetchall()
        claimed = [(row["lead_key"], row["step"]) for row in rows]
        self.db.executemany(
            "UPDATE jobs SET status = 'running' WHERE lead_key = ? AND step = ?", claimed
        )
        self.db.commit()
        return claimed

    def finish_job(self, lead_key: str, step: str, status: str) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ? WHERE lead_key = ? AND step = ?", (status, lead_key, step)
        )
        self.db.commit()

    def cancel_pending(self, lead_key: str) -> int:
        cursor = self.db.execute(
            "UPDATE jobs SET status = 'cancelled' WHERE lead_key = ? AND status = 'pending'",
            (lead_key,),
        )
        self.db.commit()
        return cursor.rowcount

    def jobs(self, lead_key: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        args: tuple[Any, ...] = ()
        if lead_key is not None:
            sql, args = sql + " WHERE lead_key = ?", (lead_key,)
        rows = self.db.execute(sql + " ORDER BY due_at, lead_key, step", args).fetchall()
        return [dict(row) | {"due_at": from_iso(row["due_at"])} for row in rows]

    # event log and dead letters
    def log(self, now: datetime, lead_key: str | None, kind: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO events (ts, lead_key, kind, detail) VALUES (?, ?, ?, ?)",
            (iso(now), lead_key, kind, detail),
        )
        self.db.commit()

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM events ORDER BY id").fetchall()
        found = [dict(row) | {"ts": from_iso(row["ts"])} for row in rows]
        return [event for event in found if kind is None or event["kind"] == kind]

    def dead_letter(
        self, now: datetime, stage: str, lead_key: str | None, reason: str, payload: Any
    ) -> None:
        self.db.execute(
            "INSERT INTO dead_letters (ts, stage, lead_key, reason, payload) VALUES (?, ?, ?, ?, ?)",
            (iso(now), stage, lead_key, reason, json.dumps(payload, default=str, sort_keys=True)),
        )
        self.db.commit()

    def dead_letters(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT * FROM dead_letters ORDER BY id").fetchall()
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in rows]
