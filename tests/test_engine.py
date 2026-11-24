from __future__ import annotations

from datetime import timedelta

from lead_followup import Config, Engine, Store
from lead_followup.ports import (MockCRM, MockMessenger, MockNotifier, PermanentError,
                                 TransientError)

from conftest import PHONE, T0, lead

KEY = "phone:" + PHONE
DAY = timedelta(days=1)


def kinds(rig):
    return [event["kind"] for event in rig.store.events()]


def run_days(rig, days, start=T0):
    for hour in range(days * 24 + 1):
        rig.engine.run_due(start + timedelta(hours=hour))


def test_new_lead_is_acknowledged_recorded_announced_and_scheduled(rig):
    result = rig.engine.handle_lead(lead(), T0)

    assert (result.status, result.key) == ("accepted", KEY)
    assert rig.crm.records[KEY]["email"] == "alex.sample@example.com"
    assert len(rig.messenger.sent) == 1
    ack = rig.messenger.sent[0]
    assert (ack["channel"], ack["to"]) == ("sms", PHONE)
    assert "Hi Alex" in ack["body"] and "gutter cleaning" in ack["body"]
    assert rig.notifier.messages == ["New lead: Alex Sample (gutter cleaning) via website"]
    assert [(job["step"], job["status"]) for job in rig.store.jobs(KEY)] == [
        ("follow_up_1", "pending"), ("follow_up_2", "pending"),
        ("follow_up_3", "pending"), ("close", "pending")]


def test_silent_lead_gets_three_follow_ups_then_closes(rig):
    rig.engine.handle_lead(lead(), T0)
    run_days(rig, 11)

    assert len(rig.messenger.sent) == 4  # ack plus three follow ups
    assert "last note" in rig.messenger.sent[-1]["body"]
    assert rig.store.get_lead(KEY)["status"] == "closed_no_response"
    assert {job["status"] for job in rig.store.jobs(KEY)} == {"done"}


def test_follow_up_is_not_sent_early(rig):
    rig.engine.handle_lead(lead(), T0)
    assert rig.engine.run_due(T0 + DAY - timedelta(minutes=1)) == 0
    assert rig.engine.run_due(T0 + DAY) == 1


def test_running_the_same_tick_twice_does_not_send_twice(rig):
    rig.engine.handle_lead(lead(), T0)
    rig.engine.run_due(T0 + DAY)
    rig.engine.run_due(T0 + DAY)
    assert len(rig.messenger.sent) == 2


def test_duplicate_inside_window_starts_nothing(rig):
    rig.engine.handle_lead(lead(), T0)
    result = rig.engine.handle_lead(lead(phone="555.555.0101", email=None), T0 + 2 * DAY)

    assert result.status == "duplicate"
    assert len(rig.messenger.sent) == 1
    assert len(rig.store.jobs(KEY)) == 4
    assert "duplicate" in kinds(rig)


def test_same_contact_after_the_window_restarts_the_sequence(rig):
    rig.engine.handle_lead(lead(), T0)
    run_days(rig, 11)
    later = T0 + 31 * DAY
    result = rig.engine.handle_lead(lead(), later)

    assert result.status == "accepted"
    assert rig.store.get_lead(KEY)["status"] == "new"
    assert {job["status"] for job in rig.store.jobs(KEY)} == {"pending"}


def test_invalid_lead_is_rejected_dead_lettered_and_alerted(rig):
    result = rig.engine.handle_lead({"name": "Casey Placeholder", "phone": "12345"}, T0)

    assert (result.status, result.detail) == ("rejected", "no usable phone or email")
    letter = rig.store.dead_letters()[0]
    assert (letter["stage"], letter["payload"]["name"]) == ("validation", "Casey Placeholder")
    assert rig.messenger.sent == [] and rig.store.jobs() == []
    assert "Lead rejected" in rig.notifier.messages[0]


def test_reply_stops_the_sequence_and_hands_over_to_a_person(rig):
    rig.engine.handle_lead(lead(), T0)
    rig.engine.run_due(T0 + DAY)
    assert rig.engine.handle_reply("(555) 555-0101", "Yes, Tuesday works", T0 + 2 * DAY) == "replied"
    run_days(rig, 11)

    assert len(rig.messenger.sent) == 2  # ack and follow up 1 only
    assert rig.store.get_lead(KEY)["status"] == "replied"
    assert [job["status"] for job in rig.store.jobs(KEY)] == ["done", "cancelled", "cancelled", "cancelled"]
    assert "over to a person" in rig.notifier.messages[-1]


def test_stop_reply_opts_out_without_pinging_the_team(rig):
    rig.engine.handle_lead(lead(), T0)
    before = len(rig.notifier.messages)
    assert rig.engine.handle_reply(PHONE, " stop ", T0 + timedelta(hours=1)) == "opted_out"
    run_days(rig, 11)

    assert len(rig.messenger.sent) == 1
    assert len(rig.notifier.messages) == before


def test_reply_by_email_matches_and_unknown_sender_does_not(rig):
    rig.engine.handle_lead(lead(), T0)
    assert rig.engine.handle_reply("nobody@example.com", "hello", T0) == "unmatched"
    assert rig.engine.handle_reply("Alex.Sample@example.com", "hello", T0) == "replied"


def test_booked_lead_gets_no_more_messages(rig):
    rig.engine.handle_lead(lead(), T0)
    rig.engine.mark(KEY, "booked", T0 + timedelta(hours=3))
    assert rig.engine.run_due(T0 + 11 * DAY) == 0
    assert len(rig.messenger.sent) == 1


def test_transient_failure_is_retried_with_backoff(rig):
    rig.messenger.transient_failures[PHONE] = 2
    result = rig.engine.handle_lead(lead(), T0)

    assert result.detail == "sequence scheduled"
    assert rig.sleeps == [2.0, 4.0]
    assert rig.messenger.sent[0]["channel"] == "sms"
    assert kinds(rig).count("retry") == 2


def test_transient_failure_past_the_attempt_limit_falls_back_to_email(rig):
    rig.messenger.transient_failures[PHONE] = 3
    rig.engine.handle_lead(lead(), T0)

    assert rig.sleeps == [2.0, 4.0]
    assert [(m["channel"], m["to"]) for m in rig.messenger.sent] == [("email", "alex.sample@example.com")]
    assert rig.store.dead_letters() == []


def test_permanent_sms_failure_falls_back_to_email_without_retrying(rig):
    rig.messenger.permanent_failures.add(PHONE)
    rig.engine.handle_lead(lead(), T0)
    run_days(rig, 11)

    assert rig.sleeps == []
    assert [m["channel"] for m in rig.messenger.sent] == ["email"] * 4
    assert rig.store.get_lead(KEY)["status"] == "closed_no_response"


def test_all_channels_failing_dead_letters_alerts_and_stops(rig):
    rig.messenger.permanent_failures.add(PHONE)
    result = rig.engine.handle_lead(lead(email=None), T0)

    assert (result.status, result.detail) == ("accepted", "unreachable")
    letter = rig.store.dead_letters()[0]
    assert letter["stage"] == "send" and "rejected by provider" in letter["reason"]
    assert rig.store.get_lead(KEY)["status"] == "unreachable"
    assert rig.store.jobs(KEY) == []
    assert "Could not reach Alex Sample" in rig.notifier.messages[-1]


def test_follow_up_failure_mid_sequence_cancels_the_rest(rig):
    rig.engine.handle_lead(lead(email=None), T0)
    rig.messenger.permanent_failures.add(PHONE)
    run_days(rig, 11)

    assert [job["status"] for job in rig.store.jobs(KEY)] == ["failed", "cancelled", "cancelled", "cancelled"]
    assert len(rig.store.dead_letters()) == 1


def test_crm_outage_is_recorded_but_does_not_block_the_customer(rig):
    rig.crm.fail_with = TransientError("CRM timeout")
    result = rig.engine.handle_lead(lead(), T0)

    assert result.detail == "sequence scheduled"
    assert len(rig.messenger.sent) == 1
    assert rig.store.dead_letters()[0]["stage"] == "crm_upsert"
    assert any("CRM upsert failed" in message for message in rig.notifier.messages)


def test_broken_team_channel_never_raises(rig):
    rig.notifier.fail_with = PermanentError("channel archived")
    result = rig.engine.handle_lead(lead(), T0)

    assert result.status == "accepted"
    assert "notify_failed" in kinds(rig)


def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "leads.db")
    first = Engine(Store(path), MockMessenger(), MockCRM(), MockNotifier(), Config())
    first.handle_lead(lead(), T0)
    first.store.close()

    messenger = MockMessenger()
    second = Engine(Store(path), messenger, MockCRM(), MockNotifier(), Config())
    assert second.handle_lead(lead(), T0 + timedelta(hours=1)).status == "duplicate"
    assert second.run_due(T0 + DAY) == 1
    assert "checking in" in messenger.sent[0]["body"]
    second.store.close()


def test_late_night_lead_is_acknowledged_now_and_followed_up_at_8am(rig):
    night = T0 + timedelta(hours=14, minutes=30)  # 23:30 local
    rig.engine.handle_lead(lead(), night)

    assert len(rig.messenger.sent) == 1
    first_due = rig.store.jobs(KEY)[0]["due_at"]
    assert (first_due + timedelta(hours=-5)).strftime("%d %H:%M") == "07 08:00"


def test_stop_with_punctuation_or_mixed_case_is_still_an_opt_out(rig):
    rig.engine.handle_lead(lead(), T0)
    assert rig.engine.handle_reply(PHONE, "Stop.", T0 + timedelta(hours=1)) == "opted_out"


def test_reply_after_opt_out_keeps_the_opt_out(rig):
    rig.engine.handle_lead(lead(), T0)
    rig.engine.handle_reply(PHONE, "STOP", T0 + timedelta(hours=1))
    before = len(rig.notifier.messages)

    assert rig.engine.handle_reply(PHONE, "ok thanks", T0 + timedelta(hours=2)) == "opted_out"
    assert rig.store.get_lead(KEY)["status"] == "opted_out"
    assert len(rig.notifier.messages) == before


def test_overdue_follow_up_found_at_night_waits_for_the_send_window(rig):
    rig.engine.handle_lead(lead(), T0)
    night = T0 + DAY + timedelta(hours=17)  # scheduler was down, back at 02:00 local

    assert rig.engine.run_due(night) == 0
    assert len(rig.messenger.sent) == 1  # the ack only
    job = rig.store.jobs(KEY)[0]
    assert (job["step"], job["status"]) == ("follow_up_1", "pending")
    assert (job["due_at"] + timedelta(hours=-5)).strftime("%d %H:%M") == "07 08:00"
    assert rig.engine.run_due(job["due_at"]) == 1
    assert len(rig.messenger.sent) == 2


def test_scheduler_gap_sends_one_follow_up_not_a_burst(rig):
    rig.engine.handle_lead(lead(), T0)

    assert rig.engine.run_due(T0 + 8 * DAY) == 1  # all three follow ups are overdue
    assert len(rig.messenger.sent) == 2  # ack plus one follow up
    assert "last note" in rig.messenger.sent[-1]["body"]
    assert [job["status"] for job in rig.store.jobs(KEY)] == ["skipped", "skipped", "done", "pending"]
