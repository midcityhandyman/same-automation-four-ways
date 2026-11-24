from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lead_followup import Config, Engine, Store
from lead_followup.ports import MockCRM, MockMessenger, MockNotifier

# Monday 09:00 local at the default UTC-5 offset.
T0 = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)
PHONE = "+15555550101"


def lead(**overrides):
    payload = {"name": "Alex Sample", "phone": "(555) 555-0101",
               "email": "alex.sample@example.com", "service": "gutter cleaning",
               "source": "website"}
    payload.update(overrides)
    return payload


@pytest.fixture
def rig():
    """An engine wired to mocks, plus the list of backoff sleeps it asked for."""
    store, messenger, crm, notifier = Store(), MockMessenger(), MockCRM(), MockNotifier()
    sleeps: list[float] = []
    engine = Engine(store, messenger, crm, notifier, Config(), sleep=sleeps.append)
    return SimpleNamespace(engine=engine, store=store, messenger=messenger, crm=crm,
                           notifier=notifier, sleeps=sleeps)
