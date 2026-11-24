# Zapier zap outline

An outline, not an exported Zap. Step ids (S1 to S9) refer to `docs/spec.md`.
App and step names follow the Zapier editor as I know it: confirm them against
the current app before building. Paths and multi step Zaps depend on the plan.

One Zap, with Paths for the branches and Delay Until for the timing.

## Zap: Lead follow up

| # | App and event | Step | What it does | Counted as |
| --- | --- | --- | --- | --- |
| T | Webhooks by Zapier: Catch Hook | S1 | Receives the payload. | 0 (trigger, assumed free) |
| 1 | Code by Zapier: Run Javascript | S2 | The same logic as `n8n/normalize_lead.js`, reading `inputData` instead of `$input`: outputs `valid`, `reason`, `key`, normalized contact fields and the four due times. | 1 task |
| 2 | Paths | S2 | Path A: `valid` is false. Path B: `valid` is true. | 0 (assumed) |

### Path A: invalid

| # | App and event | Step | What it does | Counted as |
| --- | --- | --- | --- | --- |
| A1 | Webhooks by Zapier: POST | S9 | Sends the payload and reason to the team webhook. This alert is the dead letter. | 1 |

### Path B: valid

| # | App and event | Step | What it does | Counted as |
| --- | --- | --- | --- | --- |
| B1 | Webhooks by Zapier: POST | S3 | CRM lookup by lead key. | 1 |
| B2 | Paths | S3 | Path C: seen inside 30 days. Path D: new. | 0 (assumed) |
| C1 | Webhooks by Zapier: POST | S3 | Logs a duplicate touch in the CRM. | 1 |
| D1 | Webhooks by Zapier: POST | S4 | CRM upsert. Error handler: alert the team, then continue. | 1 |
| D2 | Webhooks by Zapier: POST | S5 | Sends the acknowledgement on the primary channel. Error handler: see below. | 1 |
| D3 | Webhooks by Zapier: POST | S6 | Posts "New lead" to the team webhook. | 1 |
| D4 | Delay by Zapier: Delay Until | S7 | Waits until `follow_up_1_at`. | 0 (assumed) |
| D5 | Webhooks by Zapier: POST | S7 | Reads the lead status from the CRM. | 1 |
| D6 | Filter by Zapier | S7 | Continues only if status is `new`. | 0 (assumed) |
| D7 | Webhooks by Zapier: POST | S7 | Sends follow up 1. | 1 |
| D8 to D11 | Delay Until, POST, Filter, POST | S7 | The same four steps for follow up 2. | 2 |
| D12 to D15 | Delay Until, POST, Filter, POST | S7 | The same four steps for follow up 3. | 2 |
| D16 | Delay by Zapier: Delay Until | S8 | Waits until `close_at`. | 0 (assumed) |
| D17 | Webhooks by Zapier: POST | S8 | Reads the lead status. | 1 |
| D18 | Filter by Zapier | S8 | Continues only if status is `new`. | 0 (assumed) |
| D19 | Webhooks by Zapier: POST | S8 | Sets `closed_no_response` in the CRM. | 1 |

Duplicate path: steps 1, B1, C1 = **3 tasks**.
Full path for a lead that never replies: step 1, B1, D1, D2, D3, then two per
follow up (6), then D17 and D19 = **13 tasks**.

## Failure handling (S9)

- **Transient send failure**: turn on Autoreplay so a failed step is retried
  on Zapier's own schedule. The schedule is fixed by Zapier: you cannot ask
  for exactly 3 attempts at 2 and 4 seconds, so this is the loosest match to
  the spec of the four.
- **Channel fallback**: add a custom error handler to D2 and to each follow up
  send. Its branch calls the gateway again with `channel = email`.
- **Every channel failed**: the fallback step's own error handler posts to the
  team webhook and sets `unreachable` in the CRM. The Zap run then ends, which
  also stops the sequence.
- **Rejected payload**: Path A.
- **Zap level**: a second, tiny Zap triggered by Zapier Manager ("new Zap
  error") posts to the team webhook, so an error nobody planned for still
  reaches a person.

## Things to know before building

- A Filter that stops a run is silent. That is correct for "the lead replied",
  and it is why the status check sits immediately before each send.
- A run that is waiting in a Delay step holds the data it started with. Edit
  the Zap and in flight runs may continue on the old version or fail: check
  how your plan handles it before changing a live Zap.
- Delay Until has a maximum hold time. The longest wait here is 3 days between
  steps, far inside it, but check the current limit if you stretch the cadence.
- Replies arrive on a separate small Zap (catch hook from the messaging
  gateway) that sets the CRM status. The status checks above read it.

## Not verified

This outline was written from product knowledge and was not built or run in a
Zapier account. Task counting rules are stated as assumptions: check your plan.
