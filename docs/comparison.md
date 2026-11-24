# Four ways, compared

The same spec (`docs/spec.md`) built as an n8n workflow, a Make scenario, a
Zapier zap and a Python package. This page compares them on the three things
that decide which one you still like a year later: what happens when something
breaks, what it costs to change, and what it costs to run.

Only the Python version was executed for this repo. The n8n JSON is checked
structurally and its Code node is run under Node.js, but it has not been
imported into a live n8n instance. The Make and Zapier files are outlines.
Statements about those three products are from working knowledge, not from a
test run here, and plan features change: verify before you rely on them.

## 1. Failure handling

| Failure | n8n | Make | Zapier | Python |
| --- | --- | --- | --- | --- |
| Transient send failure | Node setting: retry on fail, 3 tries, fixed 2s wait. No exponential backoff without a loop. | Break error handler with a retry count and interval. Needs incomplete executions switched on. | Autoreplay on Zapier's schedule. Attempt count and spacing are not yours to set. | `with_retry`: 3 attempts, 2s then 4s, both configurable and unit tested. |
| Fall back to another channel | Error output of the send node wired to an email node. Visible on the canvas. | Second error handler route, then Resume. | Custom error handler branch on the step. | A loop over channels in `Engine._send`. |
| Every channel failed | Error output of the fallback goes to an alert node and a status update. | Fallback's error route alerts, marks unreachable, commits. | Fallback's error handler alerts and updates status. | Dead letter row, team alert, status `unreachable`, pending jobs cancelled. One code path, one test. |
| Bad payload | IF node, alert, 422 response. | Router route, alert, 422 response. | Path A, alert. The form gets no error back from a catch hook. | `ValidationError`, dead letter row with the payload, alert. |
| CRM down | Error output continues to the acknowledgement. | Resume handler. | Error handler, then continue. | Caught, dead lettered, sequence continues. |
| Crash or redeploy mid sequence | Waiting executions are stored in the n8n database and resume, provided the instance comes back and execution data was not pruned. | Jobs are rows in a data store, so nothing is in flight. This is the most robust of the three hosted designs, at the cost of polling. | In flight runs are held by Zapier. Editing the Zap while runs wait is the risk. | Jobs are rows in SQLite. A job is claimed before it is sent, so a crash mid send leaves it in `running` for review and never sends twice. |
| Unplanned error | Error Trigger workflow alerts the team with the execution link. | Scenario failure email. Repeated errors can switch the scenario off. | Zap error email, plus the Zapier Manager alert Zap. | Whatever supervises the process. You must add that yourself. |
| Where the evidence lives | Execution log per run, with data at every node. | Scenario history per run, bundle by bundle. | Zap history per run. | The `events` and `dead_letters` tables. Queryable, but there is no UI unless you build one. |

What stands out:

- **The visual tools are good at the failures you draw and quiet about the
  ones you do not.** Each needs a deliberate error branch per risky step. Miss
  one and the default is a failed run in a history page that nobody is watching.
- **Long waits are the real design fork.** n8n and Zapier hold a run open for
  days. Make pushes you to a job table and a poller. Python uses the same job
  table design. Held runs are simpler to read. Job tables survive edits and
  restarts better and make "what is due tomorrow" a query.
- **Only the Python version can prove its failure handling.** `tests/` forces
  each failure and asserts the outcome. In the other three, testing a fallback
  means breaking a real connection by hand.

## 2. Maintainability

| Question | n8n | Make | Zapier | Python |
| --- | --- | --- | --- | --- |
| Who can change the message wording? | Anyone who can open the editor. | Same. | Same. Easiest of the four. | Someone who can edit a file and deploy. |
| Who can change the logic safely? | A technical operator. The Code node is plain JavaScript. | A technical operator. Normalization in nested functions is hard to read. | A technical operator. Nested Paths get deep fast. | A developer, with tests to say whether it still works. |
| Version control | JSON export diffs reasonably. This repo generates it from `n8n/build_workflow.py` so wiring changes are reviewable. | Blueprint export exists but diffs poorly. | No practical diff. Version history is inside the product. | Git, like any code. |
| Testing before go live | Pin data and run manually. | Run once with a sample bundle. | Test each step with sample data. | `pytest`, offline, in under a second. |
| Repeated steps (three follow ups) | Copy and paste nodes, or generate them as this repo does. | One runner handles any number of steps: cadence is data. | Copy and paste four steps per follow up. A fourth follow up is four more steps. | A tuple in `Config.follow_up_days`. |
| Vendor lock in | Low. Self hosting is possible and the JSON is yours. | Medium. | High. The Zap lives only in Zapier. | None beyond SQLite and the standard library. |
| Bus factor | Readable by the next operator. | Readable, two scenarios to understand. | Readable until the Paths nest. | Needs someone who reads Python. For a small company that is the main cost. |

## 3. Cost

Nothing in this section is a vendor price. The fees are placeholders so the
formula has something to work on. Put your own plan's numbers into
`src/lead_followup/cost_assumptions.json` and run
`python -m lead_followup cost` (with `PYTHONPATH=src`, or after `pip install -e .`).

### The formula

```
units        = leads * ((1 - dup_rate) * units_full_path + dup_rate * units_duplicate_path)
               + fixed_units_per_month
platform_fee = base_fee + max(0, units - included_units) * overage_per_unit
upkeep       = maintenance_hours * hourly_rate
total        = platform_fee + upkeep
```

### The inputs

| Input | n8n (hosted) | Make | Zapier | Python | Basis |
| --- | --- | --- | --- | --- | --- |
| Billing unit | execution | operation | task | none | Assumption about how each product meters |
| `units_full_path` | 1 | 23 | 13 | 0 | Counted from this repo's own workflow and outlines |
| `units_duplicate_path` | 1 | 5 | 3 | 0 | Same |
| `fixed_units_per_month` | 0 | 2,880 | 0 | 0 | Make runner polling every 15 minutes: 96 * 30 |
| `base_fee` | 25 | 10 | 30 | 8 | ASSUMPTION, placeholder |
| `included_units` | 2,500 | 10,000 | 2,000 | 0 | ASSUMPTION, placeholder |
| `overage_per_unit` | 0.01 | 0.001 | 0.02 | 0 | ASSUMPTION, placeholder |
| `maintenance_hours` per month | 1.0 | 1.5 | 1.5 | 3.0 | ASSUMPTION, a guess at upkeep effort |

Scenario assumptions: 400 leads a month, duplicate rate 0.1, upkeep valued at
60 per hour. The full path count is the worst case (a lead that never replies):
real usage is lower because replies stop the sequence early.

### Worked example with those placeholder inputs

Make, 400 leads:

```
units        = 400 * (0.9 * 23 + 0.1 * 5) + 2880 = 400 * 21.2 + 2880 = 11360
platform_fee = 10 + max(0, 11360 - 10000) * 0.001 = 10 + 1.36 = 11.36
upkeep       = 1.5 * 60 = 90.00
total        = 101.36
```

The full table for all four is in `demo/output.txt`, produced by the same code.

### What the formula shows, whatever the prices are

- **The billing unit matters more than the list price.** One lead is 1 unit on
  an execution meter, 13 on a task meter and 23 on an operation meter. Compare
  plans by units per lead, not by the number on the pricing page.
- **Polling has a floor.** The Make design spends 2,880 units a month before
  the first lead. Polling every hour instead cuts that to 720 and delays a
  follow up by up to an hour, which this use case can absorb.
- **Upkeep dominates at small volume.** With any plausible fee, an hour of a
  person's time outweighs the platform bill for a few hundred leads. The code
  version has the lowest platform fee and the highest upkeep assumption, and
  whether that trade is good depends on who is available to maintain it.

## Which one I would pick

- **Zapier** when the owner will maintain it personally, volume is low and the
  sequence is short. Fastest to a working result.
- **Make** when the cadence will change often or branch by service type: the
  job table makes cadence data, not structure.
- **n8n** when someone technical runs operations and wants the workflow in
  version control, self hosted or not.
- **Python** when the automation is core enough that a silent failure costs
  real money, and there is someone to own the code. It is the only version
  here whose failure handling is proven by tests.

A sensible path is to start in a visual tool, write the spec down as this repo
does, and move to code only when the failure cases start to matter more than
the speed of change.
