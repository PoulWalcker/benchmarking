"""Execute the submitted lifecycle through the native backend, then audit it.

This runner owns fixture application; business acceptance remains in lifecycle.py.
No reference graph or expected model answer is supplied to the backend.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
import yaml

from sapi_config_lab.runtime.lifecycle import LifecycleController, durable_json, validate_lifecycle
from sapi_config_lab.experiments.provenance import source_manifest
from sapi_config_lab.workflow import profile

if TYPE_CHECKING or __package__:
    from .contracts import require
    from .lifecycle import digest_native, verify_lifecycle
    from .verify import UniqueLoader
else:
    from contracts import require
    from lifecycle import digest_native, verify_lifecycle
    from verify import UniqueLoader


def validate_task(config: dict) -> None:
    validate_lifecycle(config)
    workflow = config["workflow"]
    require(workflow["id"] == "daily-digest" and workflow["revision"] == 1, "Wrong lifecycle task identity")
    require(config["lifecycle"]["on_test_fail"]["max_rebuilds"] == 2, "Lifecycle task requires two rebuilds maximum")
    require(config["execution"]["deadline_seconds"] == 120, "Lifecycle task deadline changed")
    require(config["activation"]["schedule"] == "0 9 * * *", "Original daily schedule changed")
    require(config["activation"]["timezone"] == "Asia/Dubai", "Original daily timezone changed")
    require(config["activation"]["rule_id"] == "daily-digest-0900", "Cron rule identity changed")
    require(config["lifecycle"]["test"]["rule_id"] == "digest-test-requested", "Callback rule identity changed")
    require(
        workflow["inputs"]
        == {
            "articles": [
                {"id": "a1", "title": "Warehouse opens", "text": "A new warehouse opened in Dubai."},
                {"id": "a2", "title": "New payment option", "text": "The shop added a payment option."},
            ]
        },
        "Submission changed the public articles",
    )
    require(
        sorted(step["uses"] for step in workflow["steps"]) == ["digest.prepare", "digest.preview", "digest.summarize"],
        "Lifecycle task requires the three registered digest operations",
    )


def verify_submission(config_path: Path, report_dir: Path, *, mode="stub", backend=None) -> dict:
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"scenario": "daily-digest", "mode": mode, "passed": False, "cases": []}
    runtime_root = Path(profile.__file__).resolve().parents[3]
    report["runtime_source_manifest"] = {
        path: value for path, value in source_manifest(runtime_root).items() if path.startswith("src/")
    }
    try:
        require(mode == "stub", "Live lifecycle requires the bounded lifecycle experiment driver")
        raw = config_path.read_bytes()
        (report_dir / "submission.yaml").write_bytes(raw)
        report["submission_sha256"] = hashlib.sha256(raw).hexdigest()
        expected = os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
        require(not expected or expected == report["submission_sha256"], "Submission hash mismatch")
        config = yaml.load(raw, Loader=UniqueLoader)
        require(isinstance(config, dict), "Submission must be a YAML object")
        validate_task(config)
        source = Path(__file__).with_name("cases.json")
        corpus = json.loads(source.read_text()) if source.exists() else {}
        if "daily-digest" not in corpus:
            from sapi_config_lab.paths import workspace_root

            source = workspace_root() / "generation/lifecycle-cases.json"
            corpus = json.loads(source.read_text())
        report["fixture_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        cases = corpus["daily-digest"]["positive"]
        for case in cases:
            candidate = copy.deepcopy(config)
            candidate["workflow"]["inputs"] = case["inputs"]
            directory = report_dir / "cases" / case["name"]
            controller = LifecycleController(directory, backend=backend)
            ref = controller.register(candidate)
            event = controller.callback(ref, "stub-test")
            require(event["state"] == "passed", "Authored candidate failed its native Callback test")
            controller.tick(datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc))
            snapshot = controller.snapshot()
            durable_json(directory / "snapshot.json", snapshot)
            checked = verify_lifecycle(snapshot, mode="stub", require_wall_clock=False)
            require(len(checked["native_executions"]) == 2, "Stub gate needs both Callback and Cron")
            report["cases"].append({"name": case["name"], "passed": True, **checked})
        # Engine-successful wrong output must be a rejected business result.
        broken = copy.deepcopy(config)
        broken["workflow"]["inputs"] = cases[-1]["inputs"]
        broken["workflow"]["output"] = {"mode": "preview", "text": "Unrelated fixed digest", "article_ids": ["wrong"]}
        directory = report_dir / "cases" / "intentional-output-mutation"
        controller = LifecycleController(directory, backend=backend)
        ref = controller.register(broken)
        event = controller.callback(ref, "wrong-output")
        snapshot = controller.snapshot()
        durable_json(directory / "snapshot.json", snapshot)
        require(event["record"]["status"] == "success", "Negative control did not succeed natively")
        checked = digest_native(broken, event["record"], event["admission"], mode="stub")
        require(checked["passed"] is False and event["state"] == "failed", "Wrong output was accepted")
        require(snapshot["families"]["daily-digest"]["active"] is None, "Rejected candidate was released")
        report["cases"].append({"name": "intentional-output-mutation", "passed": True, "business_rejected": True})
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        report["error_type"] = type(error).__name__
    # Harbor owns this shared mount; registry durability remains inside each case.
    (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
