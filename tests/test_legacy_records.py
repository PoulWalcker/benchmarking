"""Historical reads preserve recorded facts without executing archived evaluators."""

import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from sapi_config_lab.coordinate import evaluation
from sapi_config_lab.evaluate.records import read_report, recorded_path, trial_result, verifier_result
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
BASELINE = ROOT / "evidence/migration-01-baseline"


def inventory(root):
    return {str(path.relative_to(root)): sha256(path) for path in root.rglob("*") if path.is_file()}


def unpack(name, root):
    index = json.loads((BASELINE / "historical/index.json").read_text())[name]
    archive = BASELINE / "historical" / index["archive"]
    if sha256(archive) != index["sha256"]:
        raise AssertionError("Historical archive identity differs")
    with tarfile.open(archive) as stream:
        stream.extractall(root, filter="data")
    record = root / name
    if inventory(record) != index["files"]:
        raise AssertionError("Historical evidence identity differs")
    return record


class LegacyRecordTests(unittest.TestCase):
    def test_readers_import_no_execution_provider_or_lifecycle_tables(self):
        script = """
import builtins, json, sys
from pathlib import Path
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(("sapi_config_lab.execute", "sapi_config_lab.coordinate.providers", "sapi_config_lab.coordinate.scenarios", "sapi_config_lab.coordinate.lifecycle")):
        raise AssertionError(name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from sapi_config_lab.coordinate import evaluation
from sapi_config_lab.evaluate.records import read_report
root = Path(sys.argv[1])
view = read_report(root / "control-report.json")
assert len(view["trials"]) == 22
assert len({row["task_name"] for row in view["trials"]}) == 11
for row in json.loads((root / "historical-files.json").read_text()):
    path = root.parents[1] / row["path"]
    view = read_report(path)
    assert view["recorded"] == json.loads(path.read_text())
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(BASELINE)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_eleven_saved_results_preserve_execution_acceptance_quality_and_reward(self):
        path = BASELINE / "control-report.json"
        before = sha256(path)
        view = read_report(path)
        self.assertEqual(len(view["trials"]), 22)
        by_task = {}
        for trial in view["trials"]:
            by_task.setdefault(trial["task_name"], []).append(trial)
            self.assertEqual(trial["result"], trial_result(trial))
        self.assertEqual(len(by_task), 11)
        revise = next(row for row in by_task["revise-answer"] if row["result"]["acceptance"])
        digest = next(row for row in by_task["daily-digest"] if row["result"]["acceptance"])
        self.assertIs(revise["result"]["execution"], False)
        self.assertIsNone(digest["result"]["execution"])
        for name in ("checkout-recovery", "crm-lead-qualification"):
            nop = next(row for row in by_task[name] if not row["result"]["acceptance"])
            self.assertIsNone(nop["result"]["quality"]["normalized_reward"])
            self.assertIsNone(nop["rewards"])
        self.assertEqual(sha256(path), before)

    def test_archived_records_read_without_sources_and_missing_observations_stay_null(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("hosted-before-native-fields", "fixture-observation-v1"):
                record = unpack(name, root)
                before = inventory(record)
                report = json.loads((record / "evaluation/report.json").read_text())
                row = {"acceptance": report, "result_path": str(record.parent / "result.json")}
                result = trial_result(row)
                self.assertIs(result["acceptance"], True)
                if name == "hosted-before-native-fields":
                    self.assertIsNone(report.get("native_execution"))
                    self.assertIsNone(report.get("terminal_completion"))
                with self.assertRaisesRegex(ValueError, "Historical evaluator execution is deferred"):
                    evaluation.main(["--record", str(record), "--output", str(root / "derived")])
                self.assertEqual(inventory(record), before)

    def test_embedded_results_survive_report_relocation_without_trial_files(self):
        original = (BASELINE / "control-report.json").read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "report.json"
            path.write_bytes(original)
            document = json.loads(original)
            for group in ("oracle", "nop"):
                for trial in document[group]["trials"]:
                    with self.subTest(group=group, task=trial["task_name"]):
                        self.assertEqual(trial_result(trial, root=root), trial["result"])
            view = read_report(path, root=root)
            self.assertEqual(view["recorded"], document)
            self.assertEqual(path.read_bytes(), original)

    def test_relative_and_old_absolute_associations_resolve_after_run_relocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trial = root / "jobs/oracle/trial"
            trial.mkdir(parents=True)
            source = root / "environments/oracle/retired-task/evaluation/report.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({"schema": "sapi-lab-upstream-acceptance/v1"}))
            for prefix in ("", "/old/run/"):
                (root / "report.json").write_text(
                    json.dumps(
                        {
                            "trials": [
                                {
                                    "task_name": "retired-task",
                                    "result_path": prefix + "jobs/oracle/trial/result.json",
                                    "evaluation_path": prefix
                                    + "environments/oracle/retired-task/evaluation/report.json",
                                }
                            ]
                        }
                    )
                )
                self.assertEqual(
                    recorded_path(prefix + "environments/oracle/retired-task/evaluation/report.json", root), source
                )
            with self.assertRaisesRegex(ValueError, "escapes"):
                recorded_path("../outside.json", root)

    def test_missing_verdict_facts_are_not_reconstructed_as_failure(self):
        self.assertEqual(verifier_result({}, None), {"execution": None, "acceptance": None, "quality": None})
        self.assertIsNone(verifier_result({"cases": []}, None)["acceptance"])

    def test_all_committed_historical_formats_keep_their_sealed_bytes(self):
        rows = json.loads((BASELINE / "historical-files.json").read_text())
        self.assertEqual(len(rows), 78)
        for row in rows:
            with self.subTest(path=row["path"]):
                path = ROOT / row["path"]
                self.assertEqual(sha256(path), row["sha256"])
                self.assertEqual(read_report(path)["recorded"], json.loads(path.read_text()))
                self.assertEqual(sha256(path), row["sha256"])
