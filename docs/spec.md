# The automation, stated once

All four implementations build this spec. Each one tags its steps with the ids
below, and `tests/test_four_ways.py` fails if an implementation drops one.

## Input

A JSON payload from a web form or ad platform:

```json
{
  "name": "Alex Sample",
  "phone": "(555) 555-0101",
  "email": "alex.sample@example.com",
  "service": "gutter cleaning",
  "message": "Two storey house, back gutters overflow.",
  "source": "website",
  "received_at": "2026-01-05T14:00:00Z"
}
```

`name` plus at least one of `phone` or `email` is required. Everything else is
optional.

## Steps

| Id | Step | Rule |
| --- | --- | --- |
| S1 | Receive | Accept the payload on a webhook. |
| S2 | Validate and normalize | Trim the name. Phone becomes `+1XXXXXXXXXX` (10 digits, or 11 starting with 1) or stays as given if it already starts with `+` and has 8 to 15 digits. Email is lowercased and must look like an address. A lead with no name, or with neither a usable phone nor a usable email, is rejected. |
| S3 | Dedupe | The lead key is `phone:<number>` when a phone exists, otherwise `email:<address>`. A second submission with the same key inside 30 days is logged as a duplicate and starts nothing new. |
| S4 | CRM upsert | Create or update the contact. If the CRM is down, record the failure and keep going: the customer still gets an answer. |
| S5 | Acknowledge | Send an acknowledgement at once. SMS when a phone exists, otherwise email. |
| S6 | Notify the team | Post a one line "new lead" note to the team channel. |
| S7 | Follow ups | Send follow up 1, 2 and 3 at 1, 3 and 7 days after the lead arrived. Before each one, check the lead status and stop if the lead replied, booked, opted out or was unreachable. A send time outside 08:00 to 20:00 local moves to the next 08:00. |
| S8 | Close | At day 10, if the lead is still open, mark it `closed_no_response`. |
| S9 | Failure handling | Retry transient send failures up to 3 attempts with backoff (2s, then 4s). If the first channel fails, fall back to email. If every channel fails, write a dead letter, alert the team, mark the lead `unreachable` and stop its sequence. Rejected payloads also go to the dead letter queue with an alert. |

## Stop conditions

- A reply of `STOP`, `UNSUBSCRIBE`, `CANCEL`, `END` or `QUIT` sets `opted_out`.
- Any other reply sets `replied` and tells the team a person should take over.
- A booking sets `booked`.

All three cancel every pending follow up.

## What the spec leaves out on purpose

Lead scoring, message personalisation beyond first name and service, daylight
saving, and two way conversation. See "Limitations" in the README.
