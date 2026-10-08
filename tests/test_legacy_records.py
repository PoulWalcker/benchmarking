"""Historical reads retain recorded facts; only explicit matching independent snapshots may re-evaluate."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate import evaluation
from sapi_config_lab.coordinate.historical_evaluation import reevaluate_record
from sapi_config_lab.evaluate.records import read_report, recorded_path, trial_result, verifier_result
from sapi_config_lab.evaluate.review_export import find_evaluations
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root
from tests.support.pinned import AVAILABLE, SOURCE

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
from sapi_config_lab.evaluate.review_export import find_evaluations
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
                with self.assertRaisesRegex(ValueError, "source snapshot unavailable"):
                    reevaluate_record(record, root / "derived", None, None, None, None)
                self.assertEqual(inventory(record), before)

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
                self.assertEqual(find_evaluations(root / "jobs"), [(trial, source)])
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

    def test_changed_snapshot_is_refused_before_loading_any_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = unpack("fixture-observation-v1", root)
            manifest = root / "sources.json"
            manifest.write_text(json.dumps({"verification/verify.py": "0" * 64}))
            (root / "verification").mkdir()
            (root / "verification/verify.py").write_text("raise AssertionError('must not import')")
            with patch("sapi_config_lab.coordinate.historical_evaluation.load_snapshot", side_effect=AssertionError):
                with self.assertRaisesRegex(ValueError, "snapshot differs"):
                    reevaluate_record(record, root / "derived", root, manifest, None, None)

    def test_snapshot_python_bytecode_cannot_replace_verified_evaluator_bytes(self):
        import py_compile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = unpack("hosted-before-native-fields", root)
            adapter = root / "src/sapi_config_lab/evaluate/autowfbench.py"
            adapter.parent.mkdir(parents=True)
            adapter.write_text("raise AssertionError('unchecked bytecode executed')\n")
            py_compile.compile(str(adapter), invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            adapter.write_bytes((ROOT / "src/sapi_config_lab/evaluate/autowfbench.py").read_bytes())
            manifest = root / "sources.json"
            manifest.write_text(json.dumps({str(adapter.relative_to(root)): sha256(adapter)}))
            (root / "provenance").mkdir()
            shutil.copyfile(SOURCE.manifest, root / "provenance/autowfbench-source.json")
            if AVAILABLE:
                shutil.copytree(SOURCE.root, root / ".cache/autowfbench" / SOURCE.root.name)
                result = reevaluate_record(
                    record, root / "derived", root, manifest, None, record / "evaluation/judge-reply.json"
                )
                self.assertIs(result["acceptance"], True)
            else:
                with self.assertRaisesRegex(ValueError, "Missing or unsafe pinned source"):
                    reevaluate_record(record, root / "derived", root, manifest, None, None)

    @unittest.skipUnless(AVAILABLE, "Requires pinned independent upstream evaluator")
    def test_matching_saved_judgement_replays_offline_and_mismatch_never_dispatches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = unpack("hosted-before-native-fields", root)
            snapshot = root / "snapshot"
            adapter = snapshot / "src/sapi_config_lab/evaluate/autowfbench.py"
            adapter.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "src/sapi_config_lab/evaluate/autowfbench.py", adapter)
            (snapshot / "provenance").mkdir()
            shutil.copyfile(SOURCE.manifest, snapshot / "provenance/autowfbench-source.json")
            shutil.copytree(SOURCE.root, snapshot / ".cache/autowfbench" / SOURCE.root.name)
            manifest = root / "sources.json"
            manifest.write_text(json.dumps({str(adapter.relative_to(snapshot)): sha256(adapter)}))
            before = inventory(record)
            args = ["--record", str(record), "--source-root", str(snapshot), "--source-manifest", str(manifest)]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    evaluation.main(
                        [
                            *args,
                            "--output",
                            str(root / "derived"),
                            "--judgement",
                            str(record / "evaluation/judge-reply.json"),
                        ]
                    ),
                    0,
                )
            result = json.loads((root / "derived/result.json").read_text())
            original = json.loads((record / "evaluation/report.json").read_text())["result"]
            self.assertEqual(result, original)
            self.assertFalse((root / "derived/evaluation/judge-dispatch.json").exists())
            reply = json.loads((record / "evaluation/judge-reply.json").read_text())
            reply["provenance"]["run_log_digest"] = "0" * 64
            changed = root / "changed-reply.json"
            changed.write_text(json.dumps(reply))
            with self.assertRaisesRegex(ValueError, "Judge provenance differs"):
                evaluation.main([*args, "--output", str(root / "refused"), "--judgement", str(changed)])
            self.assertFalse((root / "refused/evaluation/judge-dispatch.json").exists())
            self.assertEqual(inventory(record), before)
