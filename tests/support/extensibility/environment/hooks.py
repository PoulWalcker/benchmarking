"""Bind the beacon world and freeze its observations beside engine evidence."""

import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, build_opener

from sapi_config_lab.contracts import RunBinding
from sapi_config_lab.evidence import durable_json
from sapi_config_lab.profile import read


def plan(submission, options):
    return {
        "schema": "sapi-lab-observation-plan/v1",
        "scenario": "beacon-calibration",
        "mode": options.get("mode", "stub"),
        "entries": [{"name": "calibration", "procedure": "case", "config": read(submission)}],
    }


def prepare(context):
    return RunBinding(
        deadline_at=time.time() + 30, operation_url="http://beacon:8000/tools", operation_token="beacon-private-tool"
    )


def snapshot(context):
    evidence = Path(context["evidence"])
    evidence.mkdir(parents=True, exist_ok=True)
    with build_opener(ProxyHandler({})).open("http://beacon:8000/snapshot", timeout=5) as response:
        world = json.load(response)
    durable_json(evidence / "beacon-world.json", world)
    durable_json(evidence / "beacon-completion.json", context["record"])
    return world
