"""Generate n8n/lead_follow_up.workflow.json.

The workflow is built from code so the Code node always carries the exact
contents of normalize_lead.js and so the wiring is reviewable as a short list.
Run: python n8n/build_workflow.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / "lead_follow_up.workflow.json"
LEAD = "$('Normalize Lead').item.json"
CRM = "={{ $env.CRM_BASE_URL }}"
TEMPLATES = {
    "ack": "Hi {{first}}, thanks for asking about {{service}}. We have your request and will "
           "reply within one business day. Reply STOP to opt out.",
    "follow_up_1": "Hi {{first}}, checking in on your {{service}} request. Is there a good time "
                   "for a quick call? Reply STOP to opt out.",
    "follow_up_2": "Hi {{first}}, we still have room this week for your {{service}} request. "
                   "Reply here and we will set it up. Reply STOP to opt out.",
    "follow_up_3": "Hi {{first}}, last note from us about your {{service}} request. Reply any "
                   "time and we will pick it back up. Reply STOP to opt out.",
}


def node_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "same-automation-four-ways/" + name))


class Workflow:
    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.connections: dict[str, dict] = {}

    def add(self, name: str, kind: str, version: float, step: str, parameters: dict, **extra) -> str:
        column, row = divmod(len(self.nodes), 4)
        self.nodes.append({
            "parameters": parameters, "id": node_id(name), "name": name,
            "type": f"n8n-nodes-base.{kind}", "typeVersion": version,
            "position": [column * 260, row * 180], "notes": f"Spec step {step}", **extra,
        })
        return name

    def link(self, source: str, target: str, output: int = 0) -> None:
        outputs = self.connections.setdefault(source, {"main": []})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": 0})

    def http(self, name: str, step: str, url: str, body: str, error_output: bool = False) -> str:
        extra = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000,
                 "credentials": {"httpHeaderAuth": {"id": "REPLACE_ME", "name": "Gateway token"}}}
        if error_output:
            extra["onError"] = "continueErrorOutput"
        return self.add(name, "httpRequest", 4.2, step, {
            "method": "POST", "url": url, "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth", "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify(" + body + ") }}", "options": {"timeout": 10000},
        }, **extra)

    def check(self, name: str, step: str, expression: str) -> str:
        return self.add(name, "if", 2.2, step, {"conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
            "conditions": [{"id": node_id(name + "/condition"), "leftValue": "={{ " + expression + " }}",
                            "rightValue": "", "operator": {"type": "boolean", "operation": "true",
                                                           "singleValue": True}}],
            "combinator": "and"}, "options": {}})

    def respond(self, name: str, step: str, code: int, status: str) -> str:
        return self.add(name, "respondToWebhook", 1.1, step, {
            "respondWith": "json", "responseBody": json.dumps({"status": status}),
            "options": {"responseCode": code}})


def message(template: str, channel: str, to: str) -> str:
    text = json.dumps(TEMPLATES[template]).replace("{{first}}", f'" + {LEAD}.first_name + "')
    text = text.replace("{{service}}", f'" + {LEAD}.service + "')
    return f"{{ channel: {channel}, to: {to}, body: {text} }}"


def build() -> dict:
    w = Workflow()
    msg_url, team_url = "={{ $env.MESSAGING_URL }}", "={{ $env.TEAM_WEBHOOK_URL }}"
    key = f"{{ key: {LEAD}.key }}"

    hook = w.add("Lead Webhook", "webhook", 2, "S1", {
        "httpMethod": "POST", "path": "lead-follow-up", "responseMode": "responseNode",
        "options": {}}, webhookId=node_id("webhook"))
    normalize = w.add("Normalize Lead", "code", 2, "S2",
                      {"jsCode": (HERE / "normalize_lead.js").read_text(encoding="utf-8")})
    valid = w.check("Is Valid?", "S2", "$json.valid")
    invalid_alert = w.http("Dead Letter: Invalid Lead", "S9", team_url,
                           "{ text: 'Lead rejected: ' + $json.reason, payload: $json.raw }")
    invalid_reply = w.respond("Respond 422", "S9", 422, "rejected")
    lookup = w.http("CRM Lookup", "S3", CRM + "/contacts/lookup",
                    f"{{ key: {LEAD}.key, window_days: 30 }}")
    duplicate = w.check("Is Duplicate?", "S3", "$json.found")
    touch = w.http("Log Duplicate Touch", "S3", CRM + "/contacts/touch",
                   f"{{ key: {LEAD}.key, source: {LEAD}.source }}")
    duplicate_reply = w.respond("Respond 200 Duplicate", "S3", 200, "duplicate")
    upsert = w.http("CRM Upsert", "S4", CRM + "/contacts/upsert", LEAD, error_output=True)
    crm_alert = w.http("Alert Team: CRM Failed", "S9", team_url,
                       f"{{ text: 'CRM upsert failed for ' + {LEAD}.name }}")
    send_alert = w.http("Alert Team: Send Failed", "S9", team_url,
                        f"{{ text: 'Could not reach ' + {LEAD}.name + ' on any channel' }}")
    unreachable = w.http("Mark Unreachable", "S9", CRM + "/contacts/status",
                         f"{{ key: {LEAD}.key, status: 'unreachable' }}")
    w.link(send_alert, unreachable)

    def send_pair(label: str, template: str, step: str) -> tuple[str, str]:
        """A send on the primary channel whose error output falls back to email."""
        first = w.http(f"Send {label}", step, msg_url,
                       message(template, f"{LEAD}.primary_channel", f"{LEAD}.primary_to"),
                       error_output=True)
        fallback = w.http(f"Send {label} Fallback Email", "S9", msg_url,
                          message(template, "'email'", f"{LEAD}.email"), error_output=True)
        w.link(first, fallback, output=1)
        w.link(fallback, send_alert, output=1)
        return first, fallback

    ack, ack_fallback = send_pair("Ack", "ack", "S5")
    notify = w.http("Notify Team", "S6", team_url,
                    f"{{ text: 'New lead: ' + {LEAD}.name + ' (' + {LEAD}.service + ')' }}")
    accepted = w.respond("Respond 202 Accepted", "S6", 202, "accepted")

    w.link(hook, normalize)
    w.link(normalize, valid)
    w.link(valid, lookup, output=0)
    w.link(valid, invalid_alert, output=1)
    w.link(invalid_alert, invalid_reply)
    w.link(lookup, duplicate)
    w.link(duplicate, touch, output=0)
    w.link(touch, duplicate_reply)
    w.link(duplicate, upsert, output=1)
    w.link(upsert, ack, output=0)
    w.link(upsert, crm_alert, output=1)
    w.link(crm_alert, ack)
    w.link(ack, notify)
    w.link(ack_fallback, notify)
    w.link(notify, accepted)

    previous = [accepted]
    stopped = w.add("Sequence Stopped", "noOp", 1, "S7", {})
    for index in (1, 2, 3):
        wait = w.add(f"Wait Until Follow Up {index}", "wait", 1.1, "S7", {
            "resume": "specificTime", "dateTime": f"={{{{ {LEAD}.follow_up_{index}_at }}}}"},
            webhookId=node_id(f"wait-{index}"))
        status = w.http(f"Check Status {index}", "S7", CRM + "/contacts/status/read", key)
        still_open = w.check(f"Still Open {index}?", "S7", "$json.status === 'new'")
        send, fallback = send_pair(f"Follow Up {index}", f"follow_up_{index}", "S7")
        for source in previous:
            w.link(source, wait)
        w.link(wait, status)
        w.link(status, still_open)
        w.link(still_open, send, output=0)
        w.link(still_open, stopped, output=1)
        previous = [send, fallback]

    wait = w.add("Wait Until Close", "wait", 1.1, "S8", {
        "resume": "specificTime", "dateTime": f"={{{{ {LEAD}.close_at }}}}"},
        webhookId=node_id("wait-close"))
    status = w.http("Check Status Before Close", "S8", CRM + "/contacts/status/read", key)
    still_open = w.check("Still Open At Close?", "S8", "$json.status === 'new'")
    close = w.http("Mark Closed No Response", "S8", CRM + "/contacts/status",
                   f"{{ key: {LEAD}.key, status: 'closed_no_response' }}")
    for source in previous:
        w.link(source, wait)
    w.link(wait, status)
    w.link(status, still_open)
    w.link(still_open, close, output=0)
    w.link(still_open, stopped, output=1)

    trigger = w.add("On Workflow Error", "errorTrigger", 1, "S9", {})
    error_alert = w.http("Alert Team: Workflow Error", "S9", team_url,
                         "{ text: 'Lead follow up failed at ' + $json.execution.lastNodeExecuted"
                         " + ': ' + $json.execution.error.message, url: $json.execution.url }")
    w.link(trigger, error_alert)

    return {
        "name": "Lead Follow Up (same automation, four ways)",
        "nodes": w.nodes,
        "connections": w.connections,
        "active": False,
        "settings": {"executionOrder": "v1", "saveExecutionProgress": True,
                     "saveDataErrorExecution": "all",
                     "errorWorkflow": "REPLACE_WITH_THIS_WORKFLOW_ID"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


def render() -> str:
    return json.dumps(build(), indent=2) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT.name}: {len(build()['nodes'])} nodes")
