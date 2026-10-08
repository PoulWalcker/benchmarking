"""Recompute beacon calibration independently from immutable observations."""

import hashlib
import json
from pathlib import Path


def evaluate(evidence, options):
    evidence = Path(evidence)
    world = json.loads((evidence / "beacon-world.json").read_text())
    record = json.loads((evidence / "beacon-completion.json").read_text())
    initial = world["initial"]
    expected = {
        "calibration": divmod(initial["offset"] + initial["bearing"], 360)[1],
        "station": initial["station"],
        "seal": initial["seal"],
    }
    observation = evidence / "observation.json"
    intact = observation.is_file()
    if intact:
        rows = json.loads(observation.read_text())["entries"]
        intact = len(rows) == 1 and rows[0]["name"] == "calibration"
        for row in rows:
            for relative, digest in row["files"].items():
                path = evidence / "cases" / row["name"] / relative
                intact = intact and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
    native = evidence / "cases/calibration/case.json"
    if native.is_file():
        intact = intact and json.loads(native.read_text()) == record
    execution = record.get("status") == "success"
    calls = world["calls"]
    accepted = bool(
        execution
        and intact
        and record.get("output") == expected
        and len(calls) == 1
        and calls[0]["request"]["operation"] == "beacon.sample"
        and calls[0]["request"]["arguments"] == {"station": "north"}
        and calls[0]["response"]
        == {"angle": expected["calibration"], "station": expected["station"], "seal": expected["seal"]}
    )
    result = {"execution": execution, "acceptance": accepted, "quality": None}
    output = Path(options["evaluation"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps({**result, "expected": expected}) + "\n")
    return result
