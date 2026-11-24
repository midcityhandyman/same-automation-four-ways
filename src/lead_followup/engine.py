"""The automation itself. Step ids (S1 to S9) refer to docs/spec.md."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

from .config import Config
from .normalize import (ValidationError, normalize_email, normalize_phone, parse_lead,
                        plan_schedule, quiet_adjust)
from .ports import PermanentError, TransientError, with_retry
from .store import Store

STOP_WORDS = {"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}

TEMPLATES = {
    "ack": "Hi {first}, thanks for asking about {service}. We have your request and will "
           "reply within one business day. Reply STOP to opt out.",
    "follow_up_1": "Hi {first}, checking in on your {service} request. Is there a good time "
                   "for a quick call? Reply STOP to opt out.",
    "follow_up_2": "Hi {first}, we still have room this week for your {service} request. "
                   "Reply here and we will set it up. Reply STOP to opt out.",
    "follow_up_3": "Hi {first}, last note from us about your {service} request. Reply any "
                   "time and we will pick it back up. Reply STOP to opt out.",
}


@dataclass(frozen=True)
class Result:
    status: str  # accepted, duplicate or rejected
    key: str | None
    detail: str = ""


class Engine:
    def __init__(self, store: Store, messenger: Any, crm: Any, notifier: Any,
                 config: Config | None = None,
                 sleep: Callable[[float], None] = lambda seconds: None) -> None:
        self.store, self.messenger, self.crm, self.notifier = store, messenger, crm, notifier
        self.config = config or Config()
        self.sleep = sleep

    # S1 to S7: a new lead arrives
    def handle_lead(self, payload: Mapping[str, Any], now: datetime) -> Result:
        try:
            lead = parse_lead(payload, now)  # S2
        except ValidationError as exc:
            self.store.dead_letter(now, "validation", None, str(exc), payload)  # S9
            self.store.log(now, None, "rejected", str(exc))
            self._notify(now, None, f"Lead rejected ({exc}). See the dead letter queue.")
            return Result("rejected", None, str(exc))

        existing = self.store.get_lead(lead.key)  # S3
        window = timedelta(days=self.config.dedupe_window_days)
        if existing and now - existing["created_at"] < window:
            self.store.log(now, lead.key, "duplicate", f"source={lead.source}")
            return Result("duplicate", lead.key, "inside dedupe window")

        self.store.save_lead(lead, "new", now)
        self.store.log(now, lead.key, "accepted", f"{lead.name}, {lead.service}, source={lead.source}")
        record = {"key": lead.key, "name": lead.name, "email": lead.email, "phone": lead.phone,
                  "service": lead.service, "message": lead.message, "source": lead.source}
        try:  # S4. A CRM outage must not stop the customer hearing back.
            self._retry(lambda: self.crm.upsert(record), now, lead.key, "crm_upsert")
            self.store.log(now, lead.key, "crm_upserted")
        except (TransientError, PermanentError) as exc:
            self.store.dead_letter(now, "crm_upsert", lead.key, str(exc), record)
            self.store.log(now, lead.key, "crm_failed", str(exc))
            self._notify(now, lead.key, f"CRM upsert failed for {lead.name}: {exc}")

        if not self._send(self.store.get_lead(lead.key), "ack", now):  # S5
            return Result("accepted", lead.key, "unreachable")
        self._notify(now, lead.key, f"New lead: {lead.name} ({lead.service}) via {lead.source}")  # S6
        for step, due_at in plan_schedule(lead.received_at, self.config):  # S7
            self.store.add_job(lead.key, step, due_at)
        return Result("accepted", lead.key, "sequence scheduled")

    # The stop condition: the lead answers, books or opts out
    def handle_reply(self, contact: str, text: str, now: datetime) -> str:
        lead = self.store.find_by_contact(normalize_phone(contact), normalize_email(contact))
        if lead is None:
            self.store.log(now, None, "reply_unmatched", contact)
            return "unmatched"
        # "Stop." and "stop!" are opt outs too: compare letters only.
        word = re.sub(r"[^A-Z]", "", text.upper())
        return self.mark(lead["key"], "opted_out" if word in STOP_WORDS else "replied", now)

    def mark(self, key: str, status: str, now: datetime) -> str:
        """Set a terminal status (replied, booked, opted_out) and stop the sequence."""
        lead = self.store.get_lead(key)
        if lead["status"] == "opted_out":  # an opt out is never overwritten by a later reply
            self.store.log(now, key, "reply_after_opt_out", "status kept as opted_out")
            return "opted_out"
        self.store.set_status(key, status)
        cancelled = self.store.cancel_pending(key)
        self.store.log(now, key, status, f"cancelled {cancelled} pending steps")
        if status != "opted_out":
            self._notify(now, key, f"{lead['name']} {status}. Follow ups stopped, over to a person.")
        return status

    # S7 and S8: run whatever is due
    def run_due(self, now: datetime) -> int:
        handled = 0
        claimed = self.store.claim_due(now)
        # After a scheduler gap several follow ups can be due at once. Only the
        # newest goes out, so nobody gets a burst of messages.
        newest = {key: step for key, step in claimed if step != "close"}
        in_window = quiet_adjust(now, self.config) == now
        for key, step in claimed:
            lead = self.store.get_lead(key)
            if lead is None or lead["status"] != "new":
                self.store.finish_job(key, step, "cancelled")
                continue
            if step != "close" and newest[key] != step:
                self.store.finish_job(key, step, "skipped")
                self.store.log(now, key, "skipped", f"{step} overdue, superseded by {newest[key]}")
                continue
            if step != "close" and not in_window:
                # The window is checked again at send time, not only when planning.
                self.store.add_job(key, step, quiet_adjust(now, self.config))
                self.store.log(now, key, "deferred", f"{step} held until the send window opens")
                continue
            if step == "close":  # S8
                self.store.set_status(key, "closed_no_response")
                self.store.log(now, key, "closed_no_response")
                self.store.finish_job(key, step, "done")
            else:
                sent = self._send(lead, step, now)
                self.store.finish_job(key, step, "done" if sent else "failed")
            handled += 1
        return handled

    # S5, S7 and S9: one message, with retry, channel fallback and a dead letter
    def _send(self, lead: Mapping[str, Any], template: str, now: datetime) -> bool:
        body = TEMPLATES[template].format(first=lead["name"].split()[0], service=lead["service"])
        channels = [(name, to) for name, to in (("sms", lead["phone"]), ("email", lead["email"])) if to]
        errors = []
        for channel, to in channels:
            try:
                self._retry(lambda: self.messenger.send(channel, to, body), now, lead["key"],
                            f"{template} {channel}")
                self.store.log(now, lead["key"], "sent", f"{template} via {channel}")
                return True
            except (TransientError, PermanentError) as exc:
                errors.append(f"{channel}: {exc}")
                self.store.log(now, lead["key"], "send_failed", f"{template} via {channel}: {exc}")
        reason = "; ".join(errors)
        self.store.dead_letter(now, "send", lead["key"], reason, {"template": template, "body": body})
        self.store.set_status(lead["key"], "unreachable")
        self.store.cancel_pending(lead["key"])
        self.store.log(now, lead["key"], "unreachable", "all channels failed, sequence stopped")
        self._notify(now, lead["key"], f"Could not reach {lead['name']} ({template}). {reason}")
        return False

    def _retry(self, call: Callable[[], Any], now: datetime, key: str | None, label: str) -> Any:
        def on_retry(attempt: int, delay: float, exc: Exception) -> None:
            self.store.log(now, key, "retry", f"{label}: attempt {attempt} failed ({exc}), "
                                              f"waiting {delay:g}s")

        return with_retry(call, self.config.max_attempts, self.config.backoff_base_seconds,
                          self.sleep, on_retry)

    def _notify(self, now: datetime, key: str | None, text: str) -> None:
        """Tell the team. A broken team channel is logged, never raised."""
        try:
            self.notifier.notify(text)
            self.store.log(now, key, "team_notified", text)
        except Exception as exc:  # noqa: BLE001 - the alert path must not take the run down
            self.store.log(now, key, "notify_failed", f"{text} ({exc})")
