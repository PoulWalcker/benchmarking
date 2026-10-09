"""Freeze the native verifier's research identity beside Harbor-owned evidence."""

import json
import os
from pathlib import Path

from sapi_config_lab.evidence import digest, sha256, write_json

SCHEMA = "sapi-lab-native-task/v1"


def record(name: str, output: Path, submission: Path, options: dict) -> dict:
    """Bind explicit runtime options to the complete trusted image source identity."""
    sources = json.loads(Path("/opt/source-manifest.json").read_text())
    expected = os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
    actual = sha256(submission) if submission.is_file() else None
    if expected is not None and actual != expected:
        raise ValueError("Container submission identity differs")
    if options.get("mode") not in {"stub", "live"}:
        raise ValueError("Unknown native runtime mode")
    if type(options.get("deadline_seconds")) is not int or options["deadline_seconds"] <= 0:
        raise ValueError("Invalid native deadline")
    metadata = {
        "schema": SCHEMA,
        "name": name,
        "sources": sources,
        "options": options,
        "options_sha256": digest(options),
        "submission_sha256": actual,
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "native-task.json", metadata)
    return {
        **options,
        "identity": {"task": name, "sources_sha256": digest(sources)},
        "submission": str(submission),
        "evaluation": str(output / "evaluation"),
    }
