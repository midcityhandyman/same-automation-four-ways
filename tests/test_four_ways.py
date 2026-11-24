"""Checks that hold the four implementations to the one spec."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from lead_followup import Config
from lead_followup.engine import TEMPLATES
from lead_followup.normalize import ValidationError, parse_lead, plan_schedule

from conftest import T0

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / "n8n" / "lead_follow_up.workflow.json"


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def spec_step_ids() -> list[str]:
    text = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    return re.findall(r"^\| (S\d+) \|", text, flags=re.MULTILINE)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def test_spec_defines_nine_steps():
    assert spec_step_ids() == [f"S{n}" for n in range(1, 10)]


@pytest.mark.parametrize("relative", ["make/scenario-outline.md", "zapier/zap-outline.md",
                                      "src/lead_followup/engine.py"])
def test_every_spec_step_appears_in_each_text_implementation(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    missing = [step for step in spec_step_ids() if not re.search(rf"\b{step}\b", text)]
    assert missing == []


def test_every_spec_step_appears_in_the_n8n_workflow(workflow):
    tagged = {node["notes"].removeprefix("Spec step ") for node in workflow["nodes"]}
    assert tagged == set(spec_step_ids())


def test_n8n_workflow_file_matches_its_generator():
    builder = load_module(ROOT / "n8n" / "build_workflow.py")
    assert WORKFLOW_PATH.read_text(encoding="utf-8") == builder.render()


def test_n8n_workflow_is_wired_consistently(workflow):
    names = [node["name"] for node in workflow["nodes"]]
    assert len(names) == len(set(names))
    assert len({node["id"] for node in workflow["nodes"]}) == len(names)

    targets = set()
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for link in branch:
                assert link["node"] in names
                targets.add(link["node"])
    triggers = {node["name"] for node in workflow["nodes"]
                if node["type"].endswith(("webhook", "errorTrigger"))}
    assert triggers == {"Lead Webhook", "On Workflow Error"}
    assert set(names) - targets == triggers  # nothing else is orphaned


def test_n8n_outbound_calls_retry_and_sends_have_an_error_branch(workflow):
    connections = workflow["connections"]
    for node in workflow["nodes"]:
        if not node["type"].endswith("httpRequest"):
            continue
        assert node["retryOnFail"] is True and node["maxTries"] == 3
        if node["name"].startswith("Send "):
            assert node["onError"] == "continueErrorOutput"
            assert len(connections[node["name"]]["main"]) == 2, node["name"]


def test_n8n_follow_up_sends_sit_behind_a_wait_and_a_status_check(workflow):
    connections = workflow["connections"]

    def only_target(name, output=0):
        return connections[name]["main"][output][0]["node"]

    for index in (1, 2, 3):
        assert only_target(f"Wait Until Follow Up {index}") == f"Check Status {index}"
        assert only_target(f"Check Status {index}") == f"Still Open {index}?"
        assert only_target(f"Still Open {index}?", 0) == f"Send Follow Up {index}"
        assert only_target(f"Still Open {index}?", 1) == "Sequence Stopped"
    assert only_target("Still Open At Close?", 0) == "Mark Closed No Response"


def test_n8n_messages_use_the_same_wording_as_python(workflow):
    builder = load_module(ROOT / "n8n" / "build_workflow.py")
    as_python = {name: text.replace("{{", "{").replace("}}", "}")
                 for name, text in builder.TEMPLATES.items()}
    assert as_python == TEMPLATES


PARITY_PAYLOADS = [
    {"name": "Alex Sample", "phone": "(555) 555-0101", "email": "Alex.Sample@example.com",
     "service": "gutter cleaning", "source": "website", "received_at": "2026-01-05T14:00:00Z"},
    {"name": " Blake Example ", "email": "blake@example.com", "received_at": "2026-01-06T04:30:00Z"},
    {"name": "Devon Demo", "phone": "1-555-555-0104", "received_at": "2026-01-06T10:15:00Z"},
    {"name": "Finley Mock", "phone": "+44 20 5555 0106", "received_at": "2026-01-06T01:00:00Z"},
    {"name": "Casey Placeholder", "phone": "12345", "email": "not-an-email",
     "received_at": "2026-01-05T14:00:00Z"},
    {"name": "  ", "phone": "555-555-0108", "received_at": "2026-01-05T14:00:00Z"},
]

JS_HARNESS = """
const payloads = JSON.parse(process.argv[2]);
const $env = {};
const results = payloads.map((payload) => {
  const $input = { first: () => ({ json: { body: payload } }) };
  return (function () { %s })()[0].json;
});
console.log(JSON.stringify(results));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_n8n_code_node_agrees_with_python_normalization(workflow, tmp_path):
    code = next(n for n in workflow["nodes"] if n["name"] == "Normalize Lead")["parameters"]["jsCode"]
    harness = tmp_path / "harness.js"
    harness.write_text(JS_HARNESS % code, encoding="utf-8")
    done = subprocess.run(["node", str(harness), json.dumps(PARITY_PAYLOADS)],
                          capture_output=True, text=True, check=True)
    from_js = json.loads(done.stdout)

    for payload, js in zip(PARITY_PAYLOADS, from_js):
        try:
            lead = parse_lead(payload, T0)
        except ValidationError as exc:
            assert (js["valid"], js["reason"]) == (False, str(exc))
            continue
        assert js["valid"] is True
        for field in ("key", "name", "email", "phone", "service", "message", "source"):
            assert js[field] == getattr(lead, field), field
        plan = dict(plan_schedule(lead.received_at, Config()))
        for step in ("follow_up_1", "follow_up_2", "follow_up_3", "close"):
            js_due = datetime.fromisoformat(js[f"{step}_at"].replace("Z", "+00:00"))
            assert js_due == plan[step], (payload["name"], step)


def test_saved_demo_output_is_what_the_demo_prints_today():
    demo = load_module(ROOT / "demo" / "run_demo.py")
    saved = (ROOT / "demo" / "output.txt").read_text(encoding="utf-8")
    assert demo.run() == saved


def test_cost_inputs_match_the_step_counts_in_the_outlines():
    from lead_followup.cost import load_assumptions

    by_name = {platform.name: platform for platform in load_assumptions()[1]}
    make = (ROOT / "make" / "scenario-outline.md").read_text(encoding="utf-8")
    zapier = (ROOT / "zapier" / "zap-outline.md").read_text(encoding="utf-8")
    assert f"**{by_name['Make'].units_full_path:g} operations**" in make
    assert f"**{by_name['Make'].units_duplicate_path:g} operations**" in make
    assert f"**{by_name['Zapier'].units_full_path:g} tasks**" in zapier
    assert f"**{by_name['Zapier'].units_duplicate_path:g} tasks**" in zapier


def test_repo_has_no_em_dashes():
    offenders = []
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".py", ".json", ".js", ".yml", ".txt", ".toml", ".example"}:
            if any(part in {".venv", ".deps", "node_modules", ".git"} for part in path.parts):
                continue
            if chr(0x2014) in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
