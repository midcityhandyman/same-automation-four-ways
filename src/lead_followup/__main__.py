"""Command line entry point.

    python -m lead_followup lead '{"name": "Alex Sample", "phone": "555-555-0101"}'
    python -m lead_followup reply +15555550101 "Yes please"
    python -m lead_followup run-due
    python -m lead_followup cost --leads 400

State lives in the SQLite file named by DB_PATH (default: leads.db). Without
webhook URLs in the environment every outbound call goes to an offline mock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from .config import Config
from .cost import cost_table
from .engine import Engine
from .ports import build_adapters
from .store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lead_followup", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("lead", help="handle one lead payload").add_argument("payload")
    reply = commands.add_parser("reply", help="record an inbound reply")
    reply.add_argument("contact")
    reply.add_argument("text")
    commands.add_parser("run-due", help="send every follow up that is due (run from cron)")
    commands.add_parser("cost", help="print the cost model").add_argument(
        "--leads", type=float, default=None
    )
    args = parser.parse_args(argv)

    if args.command == "cost":
        print(cost_table(args.leads))
        return 0

    store = Store(os.environ.get("DB_PATH", "leads.db"))
    engine = Engine(store, *build_adapters(os.environ), Config.from_env(), sleep=time.sleep)
    now = datetime.now(timezone.utc)
    try:
        if args.command == "lead":
            result = engine.handle_lead(json.loads(args.payload), now)
            print(f"{result.status}: {result.detail}")
            return 0 if result.status != "rejected" else 1
        if args.command == "reply":
            print(engine.handle_reply(args.contact, args.text, now))
            return 0
        print(f"{engine.run_due(now)} due steps handled")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
