"""Native evaluator subprocesses preserve identity, shared budgets and unknown judge outcomes."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.ledger import Ledger
from sapi_config_lab.coordinate.native_evaluation import reevaluate_native, validate_record
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evidence import digest, sha256, write_json
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
TASK = ROOT / "tasks/checkout-recovery"


def native_record(destination: Path, sources: dict) -> Path:
    with tarfile.open(ROOT / "evidence/migration-01-baseline/historical/hosted-before-native-fields.tar.gz") as archive:
        for entry in archive.getmembers():
            if entry.isfile():
                target = destination / entry.name.removeprefix("hosted-before-native-fields/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(entry).read())
    shutil.copyfile(TASK / "solution/config.yaml", destination / "evidence/submission.yaml")
    options = {"mode": "stub", "deadline_seconds": 120}
    write_json(
        destination / "native-task.json",
        {
            "schema": "sapi-lab-native-task/v1",
            "name": TASK.name,
            "sources": sources,
            "options": options,
            "options_sha256": digest(options),
            "submission_sha256": sha256(destination / "evidence/submission.yaml"),
        },
    )
    return destination


def mock_codex(root: Path, *, timeout: bool = False) -> Path:
    executable = root / "mock-codex"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, os, sys, time
from pathlib import Path
ledger = json.loads(Path(os.environ["SAPI_TEST_LEDGER"]).read_text())
assert ledger["events"][-1]["phase"] == "judge"
assert ledger["events"][-1]["status"] == "unknown"
assert ledger["events"][-1]["count"] == 1
with Path(os.environ["SAPI_TEST_CALLS"]).open("a") as stream:
    stream.write("reserved\\n")
"""
        + (
            "time.sleep(10)\n"
            if timeout
            else """payload = json.loads(sys.stdin.read().split("EVIDENCE_JSON:\\n", 1)[1])
run, card = payload["run_log"], payload["scorecard"]
refs = {event["source"]: event["id"] for event in reversed(run["events"])}
reply = {"run_id": run["run_id"], "scorecard_digest": payload["scorecard_digest"], "status": "complete",
    "criteria": [{"criterion_id": row["id"], "answer": "yes", "reason": "MOCKED UNPAID TEST",
        "evidence_refs": [refs[source] for source in row["required_evidence"]]}
        for row in card["criteria"] if row["evaluator"] == "llm"], "issues": []}
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(reply))
"""
        )
    )
    executable.chmod(0o755)
    return executable


class NativeEvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = source_manifest()
        self.record = native_record(self.root / "record", self.sources)

    def test_identity_mutations_refuse_before_any_task_process(self):
        path = self.record / "native-task.json"
        original = json.loads(path.read_text())
        mutations = [
            {**original, "sources": {}},
            {**original, "options_sha256": "0" * 64},
            {**original, "submission_sha256": "0" * 64},
        ]
        missing = copy.deepcopy(original)
        del missing["submission_sha256"]
        mutations.append(missing)
        for deadline in (0, -1, True):
            options = {**original["options"], "deadline_seconds": deadline}
            mutations.append({**original, "options": options, "options_sha256": digest(options)})
        with patch("sapi_config_lab.coordinate.native_evaluation.invoke") as call:
            for index, metadata in enumerate(mutations):
                write_json(path, metadata)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    reevaluate_native(self.record, self.root / ("refused-" + str(index)), None)
            call.assert_not_called()
        write_json(path, original)
        (self.record / "evidence/submission.yaml").unlink()
        with self.assertRaisesRegex(ValueError, "submission identity"):
            validate_record(self.record, TASK, self.sources)
        (self.record / "evidence/trial.json").unlink()
        write_json(path, {**original, "submission_sha256": None})
        self.assertIsNone(validate_record(self.record, TASK, self.sources)["submission_sha256"])
        del original["submission_sha256"]
        write_json(path, original)
        with self.assertRaisesRegex(ValueError, "record identity"):
            validate_record(self.record, TASK, self.sources)

    def test_actual_task_saved_reply_and_mocked_judge_share_one_durable_series(self):
        before = {str(p.relative_to(self.record)): sha256(p) for p in self.record.rglob("*") if p.is_file()}
        series = self.root / "series"
        ledger = Ledger.open(series / "ledger.json", ceilings={"judge": 1}, stop_after_failure=False)
        calls = self.root / "calls.txt"
        executable = mock_codex(self.root)
        with patch.dict(
            os.environ,
            {"CODEX_BIN": str(executable), "SAPI_TEST_LEDGER": str(ledger.path), "SAPI_TEST_CALLS": str(calls)},
        ):
            result = reevaluate_native(self.record, self.root / "offline", self.record / "evaluation/judge-reply.json")
            self.assertEqual(result["quality"]["normalized_reward"], 0.732)
            self.assertFalse(calls.exists())
            result = reevaluate_native(
                self.record,
                self.root / "mocked-judge",
                None,
                dispatch=True,
                calibration="supported-good",
                judge_model="mocked-only",
                series_dir=str(series),
                series_ceiling=None,
            )
            self.assertEqual(result["quality"]["status"], "complete")
            self.assertEqual(calls.read_text(), "reserved\n")
            with self.assertRaises(subprocess.CalledProcessError):
                reevaluate_native(
                    self.record,
                    self.root / "exhausted",
                    None,
                    dispatch=True,
                    calibration="supported-good",
                    judge_model="mocked-only",
                    series_dir=str(series),
                    series_ceiling=None,
                )
        saved = json.loads(ledger.path.read_text())
        self.assertEqual([(e["phase"], e["count"], e["status"]) for e in saved["events"]], [("judge", 1, "passed")])
        self.assertEqual(calls.read_text(), "reserved\n")
        self.assertEqual(
            before, {str(p.relative_to(self.record)): sha256(p) for p in self.record.rglob("*") if p.is_file()}
        )

    def test_actual_task_consumes_exact_pending_host_reservation_without_reserving_twice(self):
        ledger = Ledger.open(self.root / "host-ledger.json", ceilings={"judge": 1}, stop_after_failure=False)
        output = self.root / "host-judge"
        index = ledger.reserve("judge", "host/judge", 1, output / "result.json", 1)
        proof = {"ledger": str(ledger.path), "index": index, "event": ledger.data["events"][index]}
        calls = self.root / "host-calls.txt"
        executable = mock_codex(self.root)
        with patch.dict(
            os.environ,
            {"CODEX_BIN": str(executable), "SAPI_TEST_LEDGER": str(ledger.path), "SAPI_TEST_CALLS": str(calls)},
        ):
            result = reevaluate_native(
                self.record,
                output,
                None,
                dispatch=True,
                calibration="supported-good",
                judge_model="mocked-only",
                reservation=proof,
            )
        self.assertEqual(result["quality"]["status"], "complete")
        saved = json.loads(ledger.path.read_text())
        self.assertEqual(saved["events"], [proof["event"]])
        self.assertEqual(saved["events"][0]["status"], "unknown")
        ledger.finish(index, True)
        self.assertEqual(json.loads(ledger.path.read_text())["events"][0]["status"], "passed")
        self.assertEqual(calls.read_text(), "reserved\n")

    def test_actual_current_task_judge_timeout_stays_unknown_and_blocks_retry(self):
        snapshot = self.root / "snapshot"
        for relative in self.sources:
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        scoring = snapshot / "tasks/checkout-recovery/evaluation/scoring.py"
        scoring.write_text(scoring.read_text().replace("JUDGE_TIMEOUT_SECONDS = 180", "JUDGE_TIMEOUT_SECONDS = 3"))
        pin = json.loads((TASK / "provenance/autowfbench-source.json").read_text())
        upstream = ROOT / ".cache/autowfbench" / pin["revision"]
        shutil.copytree(
            upstream,
            snapshot / ".cache/autowfbench" / pin["revision"],
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
        sources = source_manifest(snapshot)
        record = native_record(self.root / "current-record", sources)
        series = self.root / "current-series"
        series.mkdir()
        ledger = series / "ledger.json"
        write_json(
            ledger,
            {
                "schema": "sapi-lab-ledger/v1",
                "source_manifest": sources,
                "ceilings": {"judge": 2},
                "stop_after_failure": False,
                "events": [],
            },
        )
        calls = self.root / "timeout-calls.txt"
        executable = mock_codex(self.root, timeout=True)
        request = {
            "dispatch": True,
            "calibration": "supported-good",
            "judge_model": "mocked-timeout",
            "series_dir": str(series),
            "series_ceiling": None,
        }

        def invoke_current(output):
            # A temporary checkout changes only the timeout constant, keeping the real task/scorer process path.
            bootstrap = "import runpy,sys; sys.path[:0]=sys.argv[1:4]; runpy.run_path(sys.argv[4],run_name='__main__')"
            task = snapshot / "tasks/checkout-recovery"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    bootstrap,
                    str(snapshot / "src"),
                    str(snapshot),
                    str(task),
                    str(task / "experiment.py"),
                ],
                input=json.dumps({**request, "action": "evaluate", "record": str(record), "output": str(output)}),
                text=True,
                capture_output=True,
                check=False,
                cwd=snapshot,
            )
            if completed.returncode == 124:
                raise subprocess.TimeoutExpired(completed.args, 3)
            if completed.returncode:
                raise ValueError(completed.stderr)
            return json.loads(completed.stdout)

        with patch.dict(
            os.environ, {"CODEX_BIN": str(executable), "SAPI_TEST_LEDGER": str(ledger), "SAPI_TEST_CALLS": str(calls)}
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                invoke_current(self.root / "timed-out")
            saved = json.loads(ledger.read_text())
            self.assertEqual([(e["count"], e["status"]) for e in saved["events"]], [(1, "unknown")])
            with self.assertRaises(ValueError):
                invoke_current(self.root / "retry-refused")
        self.assertEqual(calls.read_text(), "reserved\n")
        self.assertEqual(json.loads(ledger.read_text()), saved)
