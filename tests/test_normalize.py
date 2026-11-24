from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lead_followup import Config
from lead_followup.normalize import (ValidationError, normalize_email, normalize_phone,
                                     parse_lead, plan_schedule, quiet_adjust)

from conftest import T0, lead


@pytest.mark.parametrize("raw, expected", [
    ("(555) 555-0101", "+15555550101"),
    ("555.555.0101", "+15555550101"),
    ("1-555-555-0101", "+15555550101"),
    ("+1 555 555 0101", "+15555550101"),
    ("+44 20 5555 0101", "+442055550101"),
    ("+65 5555 0101", "+6555550101"),  # ten digits with a plus is not a US number
    ("+1234", None),
    ("12345", None),
    ("", None),
    (None, None),
    ("call me", None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("  Alex.Sample@Example.com ", "alex.sample@example.com"),
    ("not-an-email", None),
    ("two@@example.com", None),
    ("spaces in@example.com", None),
    (None, None),
])
def test_normalize_email(raw, expected):
    assert normalize_email(raw) == expected


def test_parse_lead_prefers_phone_for_the_key_and_fills_defaults():
    parsed = parse_lead({"name": "  Alex Sample ", "phone": "555-555-0101",
                         "email": "ALEX.SAMPLE@example.com"}, T0)
    assert parsed.key == "phone:+15555550101"
    assert parsed.name == "Alex Sample"
    assert parsed.email == "alex.sample@example.com"
    assert (parsed.service, parsed.source, parsed.received_at) == ("general", "unknown", T0)


def test_parse_lead_falls_back_to_an_email_key():
    assert parse_lead(lead(phone="n/a"), T0).key == "email:alex.sample@example.com"


def test_parse_lead_reads_received_at_with_z_suffix():
    parsed = parse_lead(lead(received_at="2026-01-06T04:30:00Z"), T0)
    assert parsed.received_at == datetime(2026, 1, 6, 4, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("payload, reason", [
    (lead(name="   "), "missing name"),
    (lead(phone="123", email="nope"), "no usable phone or email"),
    (lead(received_at="yesterday"), "received_at is not an ISO timestamp"),
    (["not", "an", "object"], "payload is not an object"),
])
def test_parse_lead_rejects_bad_payloads(payload, reason):
    with pytest.raises(ValidationError, match=reason):
        parse_lead(payload, T0)


def local_hour(moment, config=Config()):
    return (moment + timedelta(hours=config.utc_offset_hours)).strftime("%m-%d %H:%M")


@pytest.mark.parametrize("utc, expected_local", [
    (datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc), "01-05 09:00"),   # inside: unchanged
    (datetime(2026, 1, 6, 0, 59, tzinfo=timezone.utc), "01-05 19:59"),   # last minute inside
    (datetime(2026, 1, 6, 1, 0, tzinfo=timezone.utc), "01-06 08:00"),    # 20:00 is too late
    (datetime(2026, 1, 6, 4, 30, tzinfo=timezone.utc), "01-06 08:00"),   # 23:30 the night before
    (datetime(2026, 1, 6, 10, 15, tzinfo=timezone.utc), "01-06 08:00"),  # 05:15 same morning
])
def test_quiet_adjust_moves_sends_into_the_window(utc, expected_local):
    assert local_hour(quiet_adjust(utc, Config())) == expected_local


def test_plan_schedule_is_days_1_3_7_then_close_on_day_10():
    plan = plan_schedule(T0, Config())
    assert [step for step, _ in plan] == ["follow_up_1", "follow_up_2", "follow_up_3", "close"]
    assert [(due - T0).days for _, due in plan] == [1, 3, 7, 10]


def test_config_from_env_overrides_defaults():
    config = Config.from_env({"LOCAL_UTC_OFFSET_HOURS": "1", "MAX_ATTEMPTS": "5"})
    assert (config.utc_offset_hours, config.max_attempts, config.dedupe_window_days) == (1.0, 5, 30)
