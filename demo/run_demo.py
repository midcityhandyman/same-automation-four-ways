"""Offline simulator: eleven days of the automation in well under a second.

    python demo/run_demo.py           print the run
    python demo/run_demo.py --save    also write demo/output.txt

No network, no keys, no real clock. Time is advanced one simulated hour at a
time and every outbound call goes to a mock.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lead_followup import Config, Engine, Store  # noqa: E402
from lead_followup.cost import cost_table  # noqa: E402
from lead_followup.ports import MockCRM, MockMessenger, MockNotifier  # noqa: E402

DAYS = 11


def run() -> str:
    script = json.loads((ROOT / "demo" / "sample_leads.json").read_text(encoding="utf-8"))
    start = datetime.fromisoformat(script["start_utc"])
    config = Config()
    store, messenger, crm, notifier = Store(), MockMessenger(), MockCRM(), MockNotifier()
    messenger.permanent_failures.update(script["sms_permanent_failures"])
    messenger.transient_failures.update(script["sms_transient_failures"])
    sleeps: list[float] = []
    engine = Engine(store, messenger, crm, notifier, config, sleep=sleeps.append)

    actions = sorted(script["actions"], key=lambda action: action["at_hours"])
    for hour in range(DAYS * 24 + 1):
        tick = start + timedelta(hours=hour)
        engine.run_due(tick)
        while actions and actions[0]["at_hours"] < hour + 1:
            action = actions.pop(0)
            moment = start + timedelta(hours=action["at_hours"])
            if "lead" in action:
                engine.handle_lead(action["lead"], moment)
            else:
                engine.handle_reply(action["reply"]["from"], action["reply"]["text"], moment)

    def local(moment: datetime) -> str:
        return (moment + timedelta(hours=config.utc_offset_hours)).strftime("%a %m-%d %H:%M")

    out = [
        "LEAD FOLLOW UP: OFFLINE SIMULATION",
        f"{DAYS} simulated days, hourly ticks, mock adapters, synthetic leads.",
        f"Times are local (UTC{config.utc_offset_hours:+g}). Send window "
        f"{config.send_window_start:02d}:00 to {config.send_window_end:02d}:00.",
        "",
        "EVENT LOG",
    ]
    for event in store.events():
        out.append(f"{local(event['ts'])}  {event['lead_key'] or '-':<22}{event['kind']:<19}"
                   f"{event['detail']}".rstrip())

    out += ["", "FINAL LEAD STATUS"]
    for key in sorted({event["lead_key"] for event in store.events() if event["lead_key"]}):
        lead = store.get_lead(key)
        steps = ", ".join(f"{job['step']}={job['status']}" for job in store.jobs(key)) or "none scheduled"
        out.append(f"{lead['name']:<16}{lead['status']:<20}{steps}")

    out += ["", "DEAD LETTER QUEUE"]
    for letter in store.dead_letters():
        out.append(f"{local(datetime.fromisoformat(letter['ts']))}  {letter['stage']:<12}{letter['reason']}")

    channels = Counter(message["channel"] for message in messenger.sent)
    out += [
        "",
        "TOTALS",
        f"messages sent: {len(messenger.sent)} "
        f"({', '.join(f'{name} {count}' for name, count in sorted(channels.items()))})",
        f"team notifications: {len(notifier.messages)}",
        f"CRM records: {len(crm.records)}",
        f"dead letters: {len(store.dead_letters())}",
        f"simulated backoff sleeps (seconds): {sleeps}",
        "",
        "COST MODEL",
        cost_table(),
    ]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    text = run()
    sys.stdout.write(text)
    if "--save" in sys.argv:
        (ROOT / "demo" / "output.txt").write_text(text, encoding="utf-8", newline="\n")
