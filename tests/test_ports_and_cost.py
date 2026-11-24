from __future__ import annotations

import io
import json
import urllib.error

import pytest

from lead_followup.__main__ import main
from lead_followup.cost import Platform, cost_table, load_assumptions, monthly_cost
from lead_followup.ports import (JsonWebhook, MockCRM, MockMessenger, MockNotifier,
                                 PermanentError, TransientError, WebhookMessenger,
                                 build_adapters, with_retry)


def test_with_retry_backs_off_then_gives_up():
    sleeps, calls = [], []

    def always_down():
        calls.append(1)
        raise TransientError("down")

    with pytest.raises(TransientError):
        with_retry(always_down, attempts=4, base_delay=1.5, sleep=sleeps.append)
    assert len(calls) == 4 and sleeps == [1.5, 3.0, 6.0]


def test_with_retry_does_not_retry_permanent_errors():
    calls = []

    def rejected():
        calls.append(1)
        raise PermanentError("bad number")

    with pytest.raises(PermanentError):
        with_retry(rejected, attempts=3, base_delay=1, sleep=lambda s: None)
    assert len(calls) == 1


def test_build_adapters_defaults_to_mocks_and_switches_on_urls():
    messenger, crm, notifier = build_adapters({})
    assert isinstance(messenger, MockMessenger)
    assert isinstance(crm, MockCRM) and isinstance(notifier, MockNotifier)

    messenger, crm, _ = build_adapters({"MESSAGING_WEBHOOK_URL": "https://gateway.example.com/send"})
    assert isinstance(messenger, WebhookMessenger) and isinstance(crm, MockCRM)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_json_webhook_posts_json_with_a_bearer_token():
    seen = {}

    def opener(request, timeout):
        seen.update(url=request.full_url, body=json.loads(request.data),
                    auth=request.get_header("Authorization"), method=request.get_method())
        return FakeResponse(b"msg-1")

    hook = JsonWebhook("https://gateway.example.com/send", token="test-token", opener=opener)
    assert WebhookMessenger(hook).send("sms", "+15555550101", "hello") == "msg-1"
    assert seen == {"url": "https://gateway.example.com/send", "method": "POST",
                    "auth": "Bearer test-token",
                    "body": {"channel": "sms", "to": "+15555550101", "body": "hello"}}


@pytest.mark.parametrize("failure, expected", [
    (urllib.error.HTTPError("u", 503, "unavailable", {}, None), TransientError),
    (urllib.error.HTTPError("u", 429, "slow down", {}, None), TransientError),
    (urllib.error.HTTPError("u", 400, "bad request", {}, None), PermanentError),
    (urllib.error.URLError("no route"), TransientError),
    (TimeoutError("timed out"), TransientError),
])
def test_json_webhook_maps_failures_to_retry_or_not(failure, expected):
    def opener(request, timeout):
        raise failure

    with pytest.raises(expected):
        JsonWebhook("https://gateway.example.com/send", opener=opener).post({})


MAKE_LIKE = Platform("example", "operation", units_full_path=23, units_duplicate_path=5,
                     fixed_units_per_month=2880, base_fee=10, included_units=10000,
                     overage_per_unit=0.001, maintenance_hours=1.5)


def test_monthly_cost_matches_the_worked_example_in_the_comparison_doc():
    line = monthly_cost(MAKE_LIKE, leads=400, dup_rate=0.1, hourly_rate=60)
    assert line.units == pytest.approx(11360)
    assert line.platform_fee == pytest.approx(11.36)
    assert line.upkeep == pytest.approx(90)
    assert line.total == pytest.approx(101.36)


def test_monthly_cost_under_the_allowance_is_just_the_base_fee():
    line = monthly_cost(MAKE_LIKE, leads=10, dup_rate=0, hourly_rate=0)
    assert line.platform_fee == 10 and line.total == 10


def test_monthly_cost_rejects_nonsense_inputs():
    with pytest.raises(ValueError):
        monthly_cost(MAKE_LIKE, leads=-1, dup_rate=0.1, hourly_rate=60)
    with pytest.raises(ValueError):
        monthly_cost(MAKE_LIKE, leads=10, dup_rate=1.5, hourly_rate=60)


def test_assumptions_file_is_labelled_and_covers_all_four():
    scenario, platforms = load_assumptions()
    assert set(scenario) == {"leads_per_month", "duplicate_rate", "hourly_rate"}
    assert len(platforms) == 4
    table = cost_table()
    assert table.startswith("ASSUMPTIONS ONLY") and "Formula:" in table


def test_cli_cost_and_lead_commands_run_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cli.db"))
    for name in ("MESSAGING_WEBHOOK_URL", "CRM_WEBHOOK_URL", "TEAM_WEBHOOK_URL"):
        monkeypatch.delenv(name, raising=False)

    assert main(["cost", "--leads", "100"]) == 0
    assert "100 leads/month" in capsys.readouterr().out
    payload = json.dumps({"name": "Alex Sample", "phone": "555-555-0101"})
    assert main(["lead", payload]) == 0
    assert main(["lead", payload]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "accepted: sequence scheduled", "duplicate: inside dedupe window"]
    assert main(["lead", json.dumps({"name": ""})]) == 1
    assert main(["run-due"]) == 0
