"""Archived evaluation refuses mismatches before process dispatch and preserves old guards."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.archived_evaluation import reevaluate_versioned


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def identity(metadata):
    value = metadata["identity"]
    value["options_json"] = json.dumps(metadata["options"], sort_keys=True, separators=(",", ":"))
    value["sha256"] = hashlib.sha256(
        json.dumps([value["version"], value["benchmark_id"], value["files"], value["options_json"]]).encode()
    ).hexdigest()


class ArchivedBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.snapshot = self.root / "snapshot"
        self.manifest = self.root / "manifest.json"
        self.record = self.root / "record"
        self.output = self.root / "derived"
        declaration = {
            "id": "archived",
            "version": "sapi-lab-benchmark/v1",
            "dependencies": {},
            "public": [],
            "trusted": ["evaluator.py"],
            "reference": "reference.yaml",
        }
        save(self.snapshot / "benchmarks/01-archived/scenario.json", declaration)
        for name in ("evaluator.py", "reference.yaml"):
            (self.snapshot / "benchmarks/01-archived" / name).write_text("sealed fixture\n")
        for name in ("evaluation", "ledger", "provenance"):
            path = self.snapshot / "src/sapi_config_lab/coordinate" / (name + ".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# sealed process boundary fixture\n")
        for name in ("__init__.py", "coordinate/__init__.py"):
            (self.snapshot / "src/sapi_config_lab" / name).write_text("")
        frozen = hashes(self.snapshot)
        save(self.manifest, frozen)
        self.metadata = {
            "name": "archived",
            "options": {"mode": "stub"},
            "identity": {
                "version": declaration["version"],
                "benchmark_id": "archived",
                "files": sorted(
                    [name.removeprefix("benchmarks/01-archived/"), value]
                    for name, value in frozen.items()
                    if name.startswith("benchmarks/")
                ),
            },
            "core_files": {
                "sapi_config_lab/coordinate/evaluation.py": frozen["src/sapi_config_lab/coordinate/evaluation.py"]
            },
        }
        identity(self.metadata)
        save(self.record / "benchmark.json", self.metadata)
        self.result = {"execution": True, "acceptance": True, "quality": None}

    def evaluate(self, **kwargs):
        return reevaluate_versioned(self.record, self.output, self.snapshot, self.manifest, **kwargs)

    def test_missing_snapshot_is_required_even_for_saved_replay(self):
        for root, manifest in ((None, self.manifest), (self.snapshot, None)):
            with patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run") as run:
                with self.assertRaisesRegex(ValueError, "--source-root"):
                    reevaluate_versioned(self.record, self.output, root, manifest)
                run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_source_including_docs_fails_before_dispatch(self):
        path = self.snapshot / "docs/ARCHITECTURE.md"
        path.parent.mkdir()
        path.write_text("original")
        save(self.manifest, hashes(self.snapshot))
        path.write_text("changed")
        with patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "snapshot differs"):
                self.evaluate(dispatch=True)
            run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_record_identity_is_rejected_before_archived_python(self):
        mutations = [
            lambda m: m["options"].update(mode="live"),
            lambda m: m["core_files"].update({"sapi_config_lab/coordinate/evaluation.py": "0" * 64}),
            lambda m: m["identity"].update(sha256="0" * 64),
            lambda m: m.update(runtime_options={"source_root": "/unchecked"}),
        ]
        for mutate in mutations:
            metadata = copy.deepcopy(self.metadata)
            mutate(metadata)
            save(self.record / "benchmark.json", metadata)
            with (
                self.subTest(metadata=metadata),
                patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run") as run,
            ):
                with self.assertRaises(ValueError):
                    self.evaluate(dispatch=True)
                run.assert_not_called()

    def test_self_consistent_but_incomplete_benchmark_closure_is_rejected(self):
        self.metadata["identity"]["files"].pop()
        identity(self.metadata)
        save(self.record / "benchmark.json", self.metadata)
        with patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "closure"):
                self.evaluate(dispatch=True)
            run.assert_not_called()

    def test_symlink_and_traversal_manifest_entries_are_rejected(self):
        outside = self.root / "outside.py"
        outside.write_text("unchecked")
        (self.snapshot / "linked.py").symlink_to(outside)
        original = json.loads(self.manifest.read_text())
        for name in ("linked.py", "../outside.py"):
            save(self.manifest, {**original, name: hashlib.sha256(outside.read_bytes()).hexdigest()})
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.evaluate()

    def test_existing_output_and_evidence_destinations_are_rejected(self):
        self.output.mkdir()
        with self.assertRaises(FileExistsError):
            self.evaluate()
        with self.assertRaisesRegex(ValueError, "outside recorded evidence"):
            reevaluate_versioned(self.record, self.record / "evidence/derived", self.snapshot, self.manifest)

    def test_conflicting_judge_options_fail_without_a_process(self):
        for options in ({"dispatch": True, "judgement": self.root / "reply.json"}, {"calibration": "contentless"}):
            with (
                self.subTest(options=options),
                patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run") as run,
            ):
                with self.assertRaises(ValueError):
                    self.evaluate(**options)
                run.assert_not_called()

    def test_saved_reply_uses_clean_capture_and_no_dispatch(self):
        (self.snapshot / "__pycache__").mkdir()
        (self.snapshot / "__pycache__/evaluator.pyc").write_bytes(b"unchecked bytecode")
        reply = self.root / "reply.json"
        save(reply, {"saved": True})

        def run(command, **kwargs):
            root, _, _, _, arguments = json.loads(command[-1])
            self.assertEqual(command[1:4], ["-I", "-S", "-B"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertFalse((Path(root) / "__pycache__").exists())
            self.assertNotIn("--dispatch-judge", arguments)
            self.assertEqual(arguments[arguments.index("--judgement") + 1], str(reply))
            save(self.output / "result.json", self.result)
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            patch.dict(os.environ, {"PYTHONPATH": "/unchecked"}),
            patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run", side_effect=run),
        ):
            self.assertEqual(self.evaluate(judgement=reply), self.result)
        self.assertFalse((self.output / "ledger.json").exists())

    def test_authorized_calibration_and_series_are_forwarded_once_without_reset(self):
        series = self.root / "series"
        save(series / "ledger.json", {"events": [{"status": "unknown", "count": 1}]})
        before = (series / "ledger.json").read_bytes()

        def run(command, **kwargs):
            arguments = json.loads(command[-1])[-1]
            self.assertEqual(arguments.count("--dispatch-judge"), 1)
            for flag, value in (
                ("--calibration", "contentless"),
                ("--judge-model", "mock-model"),
                ("--series-dir", str(series)),
                ("--series-ceiling", "judge=2"),
            ):
                self.assertEqual(arguments[arguments.index(flag) + 1], value)
            raise subprocess.TimeoutExpired(command, 1)

        with patch("sapi_config_lab.coordinate.archived_evaluation.subprocess.run", side_effect=run) as invoked:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.evaluate(
                    dispatch=True,
                    calibration="contentless",
                    judge_model="mock-model",
                    series_dir=series,
                    series_ceiling=["judge=2"],
                )
            self.assertEqual(invoked.call_count, 1)
        self.assertEqual((series / "ledger.json").read_bytes(), before)
        self.assertFalse((self.output / "ledger.json").exists())


@unittest.skipUnless(os.environ.get("SAPI_ARCHIVED_EVALUATION_FIXTURE"), "Requires sealed versioned archive fixture")
class SealedArchivedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(os.environ["SAPI_ARCHIVED_EVALUATION_FIXTURE"])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_actual_saved_records_replay_without_modifying_evidence(self):
        for scenario, reward in (("invoice-total", 1.0), ("checkout-recovery", 0.732)):
            record = self.fixture / scenario
            before = hashes(record)
            result = reevaluate_versioned(
                record,
                self.root / scenario,
                self.fixture / "snapshot",
                self.fixture / "source-manifest.json",
                judgement=record / "evaluation/judge-reply.json" if scenario == "checkout-recovery" else None,
            )
            self.assertEqual(result["quality"]["normalized_reward"], reward)
            self.assertEqual(hashes(record), before)
            self.assertFalse((self.root / scenario / "evaluation/judge-dispatch.json").exists())

    def test_mock_judge_uses_archived_guard_and_one_shared_reservation(self):
        snapshot = self.root / "snapshot"
        shutil.copytree(self.fixture / "snapshot", snapshot)
        record = self.root / "record"
        shutil.copytree(self.fixture / "checkout-recovery", record)
        evaluator = next(snapshot.glob("benchmarks/*checkout-recovery/evaluation/evaluator.py"))
        # Replace only the trusted benchmark evaluator in this synthetic sealed fixture;
        # the real archived CLI, descriptor identity checks and ledger remain unchanged.
        evaluator.write_text("""import json, subprocess
from pathlib import Path

def evaluate(evidence, options):
    def score():
        if options.get("judge_model") == "mock-timeout":
            raise subprocess.TimeoutExpired("mock-judge", 1)
        return {"status": "complete"}
    if options["dispatch"]:
        options["reserved_judge"](score)
    Path(options["evaluation"]).mkdir(parents=True)
    return {"execution": True, "acceptance": True, "quality": None}
""")
        manifest = json.loads((self.fixture / "source-manifest.json").read_text())
        manifest[evaluator.relative_to(snapshot).as_posix()] = hashlib.sha256(evaluator.read_bytes()).hexdigest()
        manifest_path = self.root / "manifest.json"
        save(manifest_path, manifest)
        metadata = json.loads((record / "benchmark.json").read_text())
        for entry in metadata["identity"]["files"]:
            if entry[0] == "evaluation/evaluator.py":
                entry[1] = hashlib.sha256(evaluator.read_bytes()).hexdigest()
        identity(metadata)
        save(record / "benchmark.json", metadata)
        series = self.root / "series"
        for index in range(2):
            reevaluate_versioned(
                record,
                self.root / f"derived-{index}",
                snapshot,
                manifest_path,
                dispatch=True,
                calibration="contentless",
                judge_model="mock-model",
                series_dir=series,
                series_ceiling=["judge=2"] if index == 0 else None,
            )
        ledger = json.loads((series / "ledger.json").read_text())
        self.assertEqual(ledger["source_manifest"], manifest)
        self.assertEqual([event["count"] for event in ledger["events"]], [1, 1])
        self.assertEqual([event["status"] for event in ledger["events"]], ["passed", "passed"])
        with self.assertRaisesRegex(ValueError, "ceiling exhausted"):
            reevaluate_versioned(
                record,
                self.root / "exhausted",
                snapshot,
                manifest_path,
                dispatch=True,
                calibration="contentless",
                judge_model="mock-model",
                series_dir=series,
            )
        unknown = self.root / "unknown-series"
        with self.assertRaisesRegex(ValueError, "TimeoutExpired"):
            reevaluate_versioned(
                record,
                self.root / "timed-out",
                snapshot,
                manifest_path,
                dispatch=True,
                calibration="contentless",
                judge_model="mock-timeout",
                series_dir=unknown,
                series_ceiling=["judge=2"],
            )
        with self.assertRaisesRegex(ValueError, "unknown outcome"):
            reevaluate_versioned(
                record,
                self.root / "blocked",
                snapshot,
                manifest_path,
                dispatch=True,
                calibration="contentless",
                judge_model="mock-model",
                series_dir=unknown,
            )
        events = json.loads((unknown / "ledger.json").read_text())["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "unknown")
