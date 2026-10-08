"""Independent lifecycle test inputs and decisions, with no benchmark dependencies."""

import json
from pathlib import Path

from sapi_config_lab import profile
from sapi_config_lab.coordinate.backend import N8nBackend
from tests.support.native import SimulatedN8n
from verification.contracts import equal, require
from verification.lifecycle import native_lifecycle

ROOT = Path(__file__).parent


def config():
    return profile.read(ROOT / "config.yaml")


def bindings():
    return profile.read_bindings(ROOT / "bindings.yaml")


def acceptance(config, record):
    workflow = config["workflow"]
    step = workflow["steps"][0]
    findings = []
    if step["with"] != {"text": {"ref": "inputs.text"}}:
        findings.append("Decode must preserve the supplied source")
    if workflow["output"] != {"ref": "steps." + step["id"]}:
        findings.append("Output must reference the decoded result")
    if record.get("status") != "success" or record.get("output") != {"value": json.loads(workflow["inputs"]["text"])}:
        findings.append("Decoded value must match the supplied input")
    return {"verifier": "fixture.acceptance_v1", "passed": not findings, "findings": findings}


def native_acceptance(config, record, admission, *, mode):
    checked = native_lifecycle(config, record, admission, mode=mode)
    _, final = checked.pop("observation")
    workflow = config["workflow"]
    require(len(workflow["steps"]) == 1, "Fixture requires one decode occurrence")
    step = workflow["steps"][0]
    equal(step["uses"], "fixture.decode", "Wrong fixture operation")
    arguments = step["with"]["text"]
    text = workflow["inputs"]["text"] if arguments == {"ref": "inputs.text"} else arguments
    equal(final["steps"][step["id"]], {"value": json.loads(text)}, "Decode differs from its actual input")
    checked["passed"] = (
        arguments == {"ref": "inputs.text"}
        and workflow["output"] == {"ref": "steps." + step["id"]}
        and final["output"] == {"value": json.loads(workflow["inputs"]["text"])}
    )
    return checked


class FixtureN8n(SimulatedN8n):
    def compile(self, config, bindings, options):
        return N8nBackend((ROOT / "operations.js").read_text()).compile(config, bindings, options)
