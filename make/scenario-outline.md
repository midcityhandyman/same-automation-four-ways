# Make scenario outline

An outline, not an exported blueprint. It names the modules, the mapping that
matters and the error handler on each risky module. Step ids (S1 to S9) refer
to `docs/spec.md`. Module names follow the Make editor as I know it: confirm
them against the current app before building.

Make has no long pause module that suits a 10 day sequence, so this design
uses two scenarios and a data store: the intake scenario writes the follow up
jobs, and a scheduled runner sends whatever is due.

## Data store: `followup_jobs`

| Field | Type | Notes |
| --- | --- | --- |
| key | text | `<lead key>|<step>`, the record key, so a job cannot be written twice |
| lead_key | text | `phone:+1...` or `email:...` |
| step | text | `follow_up_1`, `follow_up_2`, `follow_up_3`, `close` |
| due_at | date | already moved into the send window |
| status | text | `pending`, `done`, `cancelled`, `failed` |
| first_name, service, phone, email | text | what the message needs |

## Scenario 1: Lead intake (instant)

| # | Module | Step | What it does | Counted as |
| --- | --- | --- | --- | --- |
| 1 | Webhooks: Custom webhook | S1 | Receives the payload. | 1 operation |
| 2 | Tools: Set multiple variables | S2 | `phone` via `replace(phone; /[^0-9]/g; emptystring)` plus a length check, `email` via `lower(trim(email))`, `valid`, `lead_key`, and the four due times. The send window rule is an `if` on `formatDate(due; "H")`. | 1 operation |
| 3 | Router | S2 | Route A when `valid` is false, route B otherwise. | 0 (assumed) |
| A1 | HTTP: Make a request | S9 | POST the rejected payload and reason to the team webhook. | 1 |
| A2 | Webhooks: Webhook response | S9 | 422 `{"status": "rejected"}`. | 1 |
| B1 | HTTP: Make a request | S3 | POST `lead_key` to the CRM lookup endpoint. | 1 |
| B2 | Router | S3 | Route C when the contact was seen inside 30 days, route D otherwise. | 0 (assumed) |
| C1 | HTTP: Make a request | S3 | POST a duplicate touch to the CRM. | 1 |
| C2 | Webhooks: Webhook response | S3 | 200 `{"status": "duplicate"}`. | 1 |
| D1 | HTTP: Make a request | S4 | CRM upsert. Error handler: Resume with an alert, so a CRM outage does not block the acknowledgement. | 1 |
| D2 | HTTP: Make a request | S5 | Send the acknowledgement on the primary channel. Error handler: see below. | 1 |
| D3 | HTTP: Make a request | S6 | Post "New lead" to the team webhook. | 1 |
| D4 | Data store: Add/replace a record (x4) | S7 | One record per job: three follow ups and the close. | 4 |
| D5 | Webhooks: Webhook response | S6 | 202 `{"status": "accepted"}`. | 1 |

Duplicate path: modules 1, 2, B1, C1, C2 = **5 operations**.
New lead intake: modules 1, 2, B1, D1 to D5 = **11 operations**.

## Scenario 2: Follow up runner (every 15 minutes)

| # | Module | Step | What it does | Counted as |
| --- | --- | --- | --- | --- |
| 1 | Data store: Search records | S7 | `status = pending` and `due_at <= now`. Runs on every poll even when nothing is due. | 1 per poll |
| 2 | HTTP: Make a request | S7, S8 | Read the lead status from the CRM. | 1 per job |
| 3 | Router | S7, S8 | Route by status and step: stopped, send, or close. | 0 (assumed) |
| 4a | HTTP: Make a request | S7 | Send the follow up. Error handler: see below. | 1 per follow up |
| 4b | HTTP: Make a request | S8 | For the `close` job: set `closed_no_response` in the CRM. | 1 per close |
| 5 | Data store: Update a record | S7, S8 | Set the job to `done` or `cancelled`. | 1 per job |

Per follow up: 3 operations, three follow ups = 9. Close: 3. With intake that
is 11 + 9 + 3 = **23 operations** for a lead that never replies.
Polling: 96 polls a day for 30 days = **2,880 operations a month** before a
single lead arrives. That fixed cost is the price of not having a long wait.

## Failure handling (S9)

- **Transient send failure**: attach a Break error handler to D2 and 4a with 3
  attempts. Break parks the bundle as an incomplete execution and retries it.
  Incomplete executions must be switched on in the scenario settings.
- **Channel fallback**: a second error handler route on the same module calls
  the gateway again with `channel = email`, then Resume.
- **Every channel failed**: the fallback's own error route posts to the team
  webhook, sets `unreachable` in the CRM, marks the remaining jobs `cancelled`
  and ends with Commit so the scenario does not show as failed forever.
- **Rejected payload**: route A above. The alert is the dead letter.
- **Scenario level**: turn on email notifications for failed runs, and set
  "number of consecutive errors" above 1 so one bad bundle does not switch the
  scenario off.

## Stop conditions

Replies arrive on a third small scenario (webhook from the messaging gateway):
it sets the CRM status and updates the lead's pending jobs to `cancelled`. It
is outside the cost count because all four implementations need the same
inbound hook.

## Not verified

This outline was written from product knowledge and was not built or run in a
Make account. Operation counting rules (in particular whether routers and
filters are free) are stated as assumptions: check your plan.
