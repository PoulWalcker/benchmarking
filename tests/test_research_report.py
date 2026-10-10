"""Unpaid contract checks for the independently scored research task."""

import contextlib
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.coordinate.evaluation import main as evaluate_main
from sapi_config_lab.coordinate.native_tasks import invoke
from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evidence import digest, sha256, write_json
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read, read_bindings, validate
from tests.support.native import SimulatedN8n

ROOT = workspace_root() / "tasks/research-report"
RUNTIME_MODEL = "gpt-6-astra"
JUDGE_MODEL = "gpt-6.1-sol"


def planned_options(**options) -> dict:
    request = {"action": "plan", "submission": str(ROOT / "solution/config.yaml"), "options": options}
    return invoke(ROOT, request, source_manifest())["options"]


def task_evaluator(task: Path):
    spec = importlib.util.spec_from_file_location(
        task.name.replace("-", "_") + "_evaluator", task / "evaluation/evaluator.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_record(destination: Path, task: Path = ROOT, **judge) -> Path:
    """A source-matched native fixture record: real simulated n8n evidence, binary reward, no Judge."""
    evaluator = task_evaluator(task)
    sources = source_manifest()
    options = {"mode": "stub", "deadline_seconds": 120, "selected_case": None, **judge}
    trusted = {**options, "identity": {"task": task.name, "sources_sha256": digest(sources)}}
    planned = evaluator.plan(task / "solution/config.yaml", trusted)
    backend = N8nBackend(operation_source=(task / "operations.js").read_text())
    backend.execute = SimulatedN8n((task / "operations.js").read_text()).execute
    observe(
        planned,
        task / "solution/config.yaml",
        destination / "evidence",
        runner=partial(run_case, backend=backend),
        bindings=read_bindings(task / "bindings.yaml"),
    )
    submission = destination / "evidence/submission.yaml"
    result = evaluator.evaluate(
        destination / "evidence",
        {**trusted, "submission": str(submission), "evaluation": str(destination / "evaluation")},
    )
    write_json(destination / "result.json", result)
    (destination / "reward.txt").write_text(str(int(result["acceptance"])) + "\n")
    write_json(
        destination / "native-task.json",
        {
            "schema": "sapi-lab-native-task/v1",
            "name": task.name,
            "sources": sources,
            "options": options,
            "options_sha256": digest(options),
            "submission_sha256": sha256(submission),
        },
    )
    return destination


def snapshot(directory: Path) -> dict:
    return {str(path.relative_to(directory)): sha256(path) for path in directory.rglob("*") if path.is_file()}


class FakeJudgeWrapper:
    """A loopback inspected wrapper: answers by path, counts every request, never reaches a provider."""

    def __init__(self, test: unittest.TestCase, root: Path):
        self.prompts: list[str] = []
        self.answers = {"clarity": "yes", "usefulness": "maybe"}
        wrapper = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                prompt = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["prompt"]
                wrapper.prompts.append(prompt)
                if self.path == "/drop":
                    self.close_connection = True
                    return
                digest_line = prompt.split("\nREQUEST_DIGEST\n", 1)[1].split("\n", 1)[0]
                answer = {
                    "request_digest": digest_line,
                    "answers": wrapper.answers,
                    "reasons": dict.fromkeys(wrapper.answers, "MOCKED UNPAID TEST"),
                    "completeness": "complete",
                }
                model = RUNTIME_MODEL if self.path == "/runtime-model" else JUDGE_MODEL
                raw = json.dumps(
                    {"ok": True, "exit_code": 0, "output": json.dumps(answer), "stderr": f"model: {model}\n"}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        test.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        test.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        # The inspected file is recorded at another machine's path, so relocation must be explicit.
        self.source = root / "judge-wrapper.py"
        self.source.write_text("# inspected judge wrapper\n")
        self.root = root

    def inspection(self, path: str = "/ok", *, model: str = JUDGE_MODEL) -> Path:
        inspection = self.root / f"judge-identity-{path.strip('/')}-{model}.json"
        inspection.write_text(
            json.dumps(
                {
                    "schema": "sapi-lab-wrapper-identity/v1",
                    "endpoint": self.base + path,
                    "dispatch": "codex-exec",
                    "response_substitution": False,
                    "wrapper_retries": 0,
                    "model": model,
                    "provider_internal_retries": "unknown",
                    "files": [
                        {
                            "name": "judge-wrapper.py",
                            "path": "/elsewhere/judge-wrapper.py",
                            "sha256": sha256(self.source),
                        }
                    ],
                }
            )
        )
        return inspection

    def arguments(self, path: str = "/ok", *, model: str = JUDGE_MODEL) -> list[str]:
        return [
            "--dispatch-judge",
            "--judge-upstream",
            self.base + path,
            "--judge-wrapper-evidence",
            str(self.inspection(path, model=model)),
            "--judge-wrapper-file",
            "judge-wrapper.py=" + str(self.source),
        ]


def evaluate_cli(*arguments: str) -> tuple[int, dict]:
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = evaluate_main(list(arguments))
    return code, json.loads(printed.getvalue())


class ResearchReportTests(unittest.TestCase):
    def test_reference_graph_and_expected_card(self):
        cfg = read(ROOT / "solution/config.yaml")
        bindings = read_bindings(ROOT / "bindings.yaml")
        order, _ = validate(cfg, bindings)
        self.assertEqual(len(order), 5)
        self.assertEqual(cfg["workflow"]["kind"], "Gantt")
        self.assertEqual(sum(step["kind"] == "LLM" for step in cfg["workflow"]["steps"]), 3)
        card = json.loads((ROOT / "evaluation/rubric.json").read_text())
        self.assertEqual(card["id"], "research-report")
        self.assertEqual(sum(c["weight"] for c in card["criteria"]), 10)
        self.assertGreaterEqual(sum(c["weight"] for c in card["criteria"] if c["evaluator"] == "deterministic"), 6)
        self.assertTrue(any(c["evaluator"] == "llm" for c in card["criteria"]))

    def test_the_versioned_card_keeps_ids_and_weights_and_binds_the_fixture_judge_prompt(self):
        from sapi_config_lab.coordinate import fixture_judge

        card = task_evaluator(ROOT).card()
        self.assertEqual((card.id, card.version, card.origin), ("research-report", "2.0.0", "local"))
        self.assertEqual(card.prompt_version, fixture_judge.PROMPT_VERSION)
        self.assertEqual(
            [(c.id, c.weight, c.evaluator) for c in card.criteria],
            [
                ("acceptance", 4, "deterministic"),
                ("grounding", 1, "deterministic"),
                ("coverage", 1, "deterministic"),
                ("clarity", 2, "llm"),
                ("usefulness", 2, "llm"),
            ],
        )
        anchors = {c.id: dict(c.anchors) for c in card.criteria if c.evaluator == "llm"}
        self.assertEqual(
            anchors,
            {
                "clarity": {
                    "yes": "Concise, organized and internally coherent. Fluency alone does not establish usefulness.",
                    "maybe": "Understandable, but repetitive or confusingly organized.",
                    "no": "Seriously confusing or contradictory.",
                },
                "usefulness": {
                    "yes": "A faithful synthesis of the product, audience and channels that preserves all important"
                    " source information and adds no unsupported factual claims.",
                    "maybe": "Faithful, but mostly copied or only weakly synthesized.",
                    "no": "Material unsupported claims, contradictions, important omissions, or irrelevant or"
                    " instructional filler that makes the report unreliable.",
                },
            },
        )

    def test_positive_and_negative_fixtures(self):
        cases = json.loads((ROOT / "cases.json").read_text())
        self.assertEqual(len(cases["positive"]), 2)
        self.assertEqual(len(cases["negative"]), 2)
        self.assertTrue(all(case["terms"] for case in cases["positive"]))


class ResearchJudgeIdentityTests(unittest.TestCase):
    def test_only_the_composition_root_reaches_the_adapter_and_wrapper_transport(self):
        from tests.test_boundaries import imported, stage_of

        root = imported(ROOT / "experiment.py", "native_tasks.research-report.experiment", False)
        self.assertTrue({"coordinate.fixture_judge", "harbor_integration.model_wrapper"} <= root)
        for name in ("evaluation/evaluator.py", "evaluation/calibration.py", "tests/main.py"):
            module = "native_tasks.research-report." + name.removesuffix(".py").replace("/", ".")
            stages = {stage_of(edge) for edge in imported(ROOT / name, module, False)}
            self.assertFalse({"execute", "harbor_integration"} & stages, name)
            self.assertNotIn("coordinate.fixture_judge", imported(ROOT / name, module, False), name)

    def test_plan_freezes_an_explicit_wrapper_judge_and_stays_identity_free_without_one(self):
        self.assertEqual(
            planned_options(mode="live", judge_model=JUDGE_MODEL),
            {
                "mode": "live",
                "deadline_seconds": 600,
                "selected_case": None,
                "judge_mode": "wrapper",
                "judge_model": JUDGE_MODEL,
            },
        )
        for unpaid in ({"mode": "stub"}, {"mode": "stub", "judge_model": None}):
            with self.subTest(unpaid=unpaid):
                self.assertEqual(
                    planned_options(**unpaid), {"mode": "stub", "deadline_seconds": 120, "selected_case": None}
                )
        for invalid in ("", "  ", 7):
            with self.subTest(invalid=invalid), self.assertRaises(subprocess.CalledProcessError):
                planned_options(mode="live", judge_model=invalid)

    def test_native_verifier_records_the_judge_identity_without_any_judge_or_grant(self):
        script = r"""
import json, os, runpy, sys
from pathlib import Path
from unittest.mock import patch
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from sapi_config_lab.coordinate import native_record
root = Path(sys.argv[2])
namespace = runpy.run_path(sys.argv[3] + "/main.py")
manifest = root / "source-manifest.json"
manifest.write_text(json.dumps({"test": "frozen"}))
def record(*args):
    with patch.object(native_record, "Path", side_effect=lambda value: manifest if value == "/opt/source-manifest.json" else Path(value)):
        return native_record.record(*args)
def execute(task, output, submission, options, functions):
    assert set(functions) == {"plan", "evaluate"} and "reserved_judge" not in options and "dispatch" not in options
    return 0
namespace["main"].__globals__.update(OUT=root / "out", SUBMISSION=root / "payload/solution/config.yaml", record=record, run_task=execute)
for env in ({"SAPI_LLM_MODE": "live", "SAPI_NATIVE_JUDGE_MODEL": sys.argv[4]}, {"SAPI_LLM_MODE": "stub"}):
    os.environ.pop("SAPI_NATIVE_JUDGE_MODEL", None)
    with patch.dict(os.environ, env):
        assert namespace["main"]() == 0
    print(json.dumps((root / "out/native-task.json").read_text()))
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT, root / "payload", ignore=shutil.ignore_patterns("__pycache__"))
            tests = [str(root / "payload/tests"), JUDGE_MODEL]
            completed = subprocess.run(
                [sys.executable, "-I", "-B", "-c", script, str(ROOT.parents[1] / "src"), str(root), *tests],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            live, stub = (json.loads(json.loads(line))["options"] for line in completed.stdout.splitlines())
            self.assertEqual((live["judge_mode"], live["judge_model"]), ("wrapper", JUDGE_MODEL))
            self.assertNotIn("judge_model", stub)
            self.assertNotIn("judge_mode", stub)


class ResearchJudgeCommandTests(unittest.TestCase):
    def setUp(self):
        # Requested Judge dispatch shows its progress display on stderr.
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wrapper = FakeJudgeWrapper(self, self.root)
        self.record = fixture_record(self.root / "record", judge_mode="wrapper", judge_model=JUDGE_MODEL)
        self.native = snapshot(self.record)

    def test_requested_fresh_judging_scores_every_criterion_once_and_keeps_binary_reward(self):
        output = self.root / "judged"
        code, result = evaluate_cli(
            "--record",
            str(self.record),
            "--output",
            str(output),
            "--judge-model",
            JUDGE_MODEL,
            *self.wrapper.arguments(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.wrapper.prompts), 1)
        self.assertIs(result["acceptance"], True)
        self.assertEqual((result["quality"]["score_0_10"], result["quality"]["normalized_reward"]), (8.66, 0.866))
        scored = json.loads((output / "evaluation/evaluation.json").read_text())
        self.assertEqual(
            {row["id"]: row["answer"] for row in scored["criteria"] if row["evaluator"] == "llm"},
            self.wrapper.answers,
        )
        self.assertEqual(scored["judge"]["model"], JUDGE_MODEL)
        receipt = json.loads((output / "judge/receipt.json").read_text())
        self.assertEqual(
            (receipt["state"], receipt["origin"], receipt["expected_model"]), ("completed", "fresh", JUDGE_MODEL)
        )
        events = json.loads((output / "ledger.json").read_text())["events"]
        self.assertEqual([(e["phase"], e["count"], e["status"]) for e in events], [("judge", 1, "passed")])
        self.assertEqual(snapshot(self.record), self.native)
        self.assertEqual((self.record / "reward.txt").read_text(), "1\n")

    def test_semantic_quality_varies_while_the_native_binary_reward_stays_one(self):
        for answer, score, normalized in (("no", 6.0, 0.6), ("maybe", 7.32, 0.732), ("yes", 10.0, 1.0)):
            with self.subTest(answer=answer):
                self.wrapper.answers = {"clarity": answer, "usefulness": answer}
                code, result, _output = self.judged(
                    "--judge-model", JUDGE_MODEL, *self.wrapper.arguments(), name=answer
                )
                self.assertEqual(code, 0)
                self.assertEqual(
                    (result["quality"]["score_0_10"], result["quality"]["normalized_reward"]), (score, normalized)
                )
                self.assertNotIn("harbor_reward", result)
                self.assertEqual((self.record / "reward.txt").read_text(), "1\n")
        self.assertEqual(snapshot(self.record), self.native)

    def judged(self, *arguments: str, name: str = "judged", record: Path | None = None) -> tuple[int, dict, Path]:
        output = self.root / name
        code, printed = evaluate_cli("--record", str(record or self.record), "--output", str(output), *arguments)
        return code, printed, output

    def assert_unspent(self, output: Path) -> None:
        self.assertEqual(self.wrapper.prompts, [])
        self.assertFalse((output / "ledger.json").exists())
        self.assertEqual(snapshot(self.record), self.native)

    def test_failed_and_unknown_judging_exit_nonzero_and_keep_acceptance_without_quality(self):
        for path, outcome, ledger in (("/runtime-model", "failed", "failed"), ("/drop", "unknown", "unknown")):
            with self.subTest(path=path):
                self.wrapper.prompts.clear()
                code, printed, output = self.judged(
                    "--judge-model", JUDGE_MODEL, *self.wrapper.arguments(path), name=path.strip("/")
                )
                self.assertEqual((code, printed["judge"]), (1, outcome))
                self.assertEqual(len(self.wrapper.prompts), 1)
                self.assertEqual((printed["result"]["acceptance"], printed["result"]["quality"]), (True, None))
                events = json.loads((output / "ledger.json").read_text())["events"]
                self.assertEqual([event["status"] for event in events], [ledger])
                self.assertEqual(snapshot(self.record), self.native)

    def test_identity_and_endpoint_substitutions_refuse_before_any_reservation_or_call(self):
        runtime_inspection = ["--judge-wrapper-evidence", str(self.wrapper.inspection(model=RUNTIME_MODEL))]
        arguments = self.wrapper.arguments()
        cases = {
            "frozen-override": ["--judge-model", RUNTIME_MODEL, *arguments],
            "runtime-inspection": ["--judge-model", JUDGE_MODEL, *arguments[:3], *runtime_inspection, *arguments[5:]],
            "substituted-endpoint": [
                "--judge-model",
                JUDGE_MODEL,
                arguments[0],
                arguments[1],
                self.wrapper.base + "/other",
                *arguments[3:],
            ],
            "unrelocated-file": ["--judge-model", JUDGE_MODEL, *arguments[:5]],
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                code, printed, output = self.judged(*case, name=name)
                self.assertEqual((code, printed["judge"], printed["result"]), (1, "failed", None))
                self.assert_unspent(output)

    def test_an_identity_free_record_names_its_judge_only_in_derived_evidence(self):
        record = fixture_record(self.root / "unpaid")
        native = (record / "native-task.json").read_bytes()
        code, _printed, output = self.judged(
            "--judge-model", JUDGE_MODEL, *self.wrapper.arguments(), name="derived", record=record
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((output / "judge/receipt.json").read_text())["expected_model"], JUDGE_MODEL)
        self.assertEqual((record / "native-task.json").read_bytes(), native)
        # The saved bundle carries its own identity; naming another Judge cannot relabel it.
        self.wrapper.prompts.clear()
        code, printed, refused = self.judged(
            "--judgement", str(output / "judge"), "--judge-model", RUNTIME_MODEL, name="relabelled", record=record
        )
        self.assertEqual((code, printed["result"]), (1, None))
        self.assertFalse((refused / "judge/replay-receipt.json").exists())
        code, _printed, _output = self.judged("--judgement", str(output / "judge"), name="replayed", record=record)
        self.assertEqual(code, 0)
        self.assertEqual(self.wrapper.prompts, [])
        endpoint = {"upstream": self.wrapper.base + "/ok", "inspection": str(self.wrapper.inspection()), "files": {}}
        request = {"record": str(record), "output": str(self.root / "no-model"), "dispatch": True, "judge": endpoint}
        self.wrapper.prompts.clear()
        # No runtime-model fallback: an identity-free record without a named Judge cannot dispatch.
        with self.assertRaises(subprocess.CalledProcessError) as refused:
            invoke(ROOT, {"action": "evaluate", **request}, source_manifest())
        self.assertIn("inspected wrapper and model", refused.exception.stderr)
        self.assertEqual(self.wrapper.prompts, [])
        self.assertFalse((self.root / "no-model/ledger.json").exists())

    def test_conflicting_or_incomplete_judge_options_fail_parsing(self):
        arguments = self.wrapper.arguments()
        cases = {
            "saved-and-dispatch": ["--judgement", str(self.root), "--dispatch-judge"],
            "endpoint-without-evidence": ["--judge-model", JUDGE_MODEL, *arguments[:3]],
            "evidence-without-endpoint": ["--judge-model", JUDGE_MODEL, "--dispatch-judge", *arguments[3:5]],
            "endpoint-without-dispatch": ["--judge-model", JUDGE_MODEL, *arguments[1:]],
            "endpoint-without-model": arguments,
            "unpaired-file": ["--judge-model", JUDGE_MODEL, *arguments[:5], "--judge-wrapper-file", "judge-wrapper.py"],
        }
        for name, case in cases.items():
            with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.judged(*case, name=name)
            self.assert_unspent(self.root / name)

    def test_tasks_without_fixture_composition_or_judge_budget_refuse_requested_judging(self):
        from tests.test_native_evaluation import native_record

        checkout = native_record(self.root / "checkout", source_manifest())
        invoice = fixture_record(self.root / "invoice", workspace_root() / "tasks/invoice-total")
        for name, record, reason in (
            ("uncomposed", checkout, "composes a fixture Judge"),
            ("unbudgeted", invoice, "no semantic judge"),
        ):
            with self.subTest(name=name):
                code, printed, output = self.judged(
                    "--judge-model", JUDGE_MODEL, *self.wrapper.arguments(), name=name, record=record
                )
                self.assertEqual(code, 1)
                self.assertIn(reason, printed["reason"])
                self.assert_unspent(output)

    def test_saved_bundle_replays_exactly_offline_and_refuses_another_identity_or_bytes(self):
        code, fresh, first = self.judged("--judge-model", JUDGE_MODEL, *self.wrapper.arguments(), name="fresh")
        self.assertEqual(code, 0)
        self.wrapper.prompts.clear()
        code, replayed, output = self.judged("--judgement", str(first / "judge"), name="replayed")
        self.assertEqual(code, 0)
        self.assertEqual(replayed["quality"]["score_0_10"], fresh["quality"]["score_0_10"])
        replay = json.loads((output / "judge/replay-receipt.json").read_text())
        self.assertEqual((replay["state"], replay["origin"], replay["new_invocations"]), ("replayed", "fresh", 0))
        self.assert_unspent(output)
        code, printed, output = self.judged(
            "--judgement", str(first / "judge"), "--judge-model", RUNTIME_MODEL, name="other-identity"
        )
        self.assertEqual((code, printed["result"]), (1, None))
        self.assert_unspent(output)
        tampered = self.root / "tampered"
        shutil.copytree(first / "judge", tampered)
        (tampered / "response.txt").write_bytes((tampered / "response.txt").read_bytes().replace(b'"maybe"', b'"yes"'))
        code, printed, output = self.judged("--judgement", str(tampered), name="tampered-replay")
        self.assertEqual((code, printed["acceptance"], printed["quality"]), (1, True, None))
        self.assert_unspent(output)

    def test_plain_evaluation_calls_no_judge_and_keeps_acceptance_exit(self):
        code, printed, output = self.judged(name="plain")
        self.assertEqual((code, printed["acceptance"], printed["quality"]), (0, True, None))
        self.assertFalse((output / "judge").exists())
        self.assert_unspent(output)


class ResearchLiveJudgeTests(unittest.TestCase):
    """`sapi-lab live` composes the Research Judge only after runtime closes, and proves it on the host."""

    RUNTIME_UPSTREAM = "http://127.0.0.1:9/runtime"

    def setUp(self):
        # Requested Judge dispatch shows its progress display on stderr.
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wrapper = FakeJudgeWrapper(self, self.root)
        source = self.root / "runtime-wrapper.py"
        source.write_text("# inspected runtime wrapper\n")
        self.runtime = self.root / "runtime-identity.json"
        self.runtime.write_text(
            json.dumps(
                {
                    **json.loads(self.wrapper.inspection(model=RUNTIME_MODEL).read_text()),
                    "endpoint": self.RUNTIME_UPSTREAM,
                    "files": [{"name": source.name, "path": str(source), "sha256": sha256(source)}],
                }
            )
        )
        self.control = self.root / "control.json"
        self.control.write_text("{}")

    def live(
        self,
        judge_arguments: list[str],
        fault: str | None = None,
        *,
        cases: tuple[str, ...] = (),
        max_calls: int = 4,
    ) -> tuple[Mock, Path]:
        """Default selection (Orion), or an explicit hypothetical cohort of these live cases."""
        from sapi_config_lab.coordinate import native_evaluation
        from sapi_config_lab.coordinate.live import main as live_main

        output = self.root / "run"
        output.mkdir()
        records = {
            case: fixture_record(
                output
                / ("jobs/live/trial/verifier" if case == "orion-clinics" else f"jobs/live-{case}/trial/verifier"),
                selected_case=case,
                judge_mode="wrapper",
                judge_model=JUDGE_MODEL,
            )
            for case in cases or ("orion-clinics",)
        }

        def trial(mode, record=records["orion-clinics"]):
            acceptance = json.loads((record / "evaluation/report.json").read_text())
            return {
                "task_name": ROOT.name,
                "result_path": str(record.parent / "result.json"),
                "exception": None,
                "rewards": {"reward": 1.0},
                "result": json.loads((record / "result.json").read_text()),
                "acceptance": acceptance,
                "native_task": {
                    "name": ROOT.name,
                    "options": {"mode": mode},
                    "submission_sha256": sha256(record / "evidence/submission.yaml"),
                },
            }

        run = Mock()
        run.step.side_effect = lambda *args, **kwargs: contextlib.nullcontext()
        run.output, run.sources = output, {}
        run.use_image.return_value = "sha256:stub"
        (output / "audit.jsonl").touch()
        run.bridge.return_value = contextlib.nullcontext(output / "audit.jsonl")
        live_trials = [trial("live", record) for record in records.values()]
        if fault == "candidate-rejected":
            live_trials[0]["result"] = {"execution": False, "acceptance": False, "quality": None}
            live_trials[0]["exception"] = {"exception_type": "RewardFileNotFoundError"}
        run.harbor.side_effect = [(0, [trial("stub")])] + [(0, [row]) for row in live_trials]

        def collected(trials, *_args, **_kwargs):
            rejected = trials[0]["result"]["acceptance"] is False
            record = {
                "status": "error",
                "persisted_status": "error",
                "result_node_present": False,
                "mapping": {"combine": "combine"},
                "run_data": {
                    "combine": [
                        {
                            "startTime": 20,
                            "error": {
                                "name": "WrappedExecutionError",
                                "message": "Unavailable reference: steps.marketing [line 30]",
                            },
                        }
                    ]
                },
            }
            return [
                {
                    "trace": "incomplete" if rejected else "complete",
                    "calls": [],
                    "record": record,
                    "graph": {"nodes": [{"name": "combine", "type": "n8n-nodes-base.code"}]},
                    "failed_node": [{"node": "combine"}] if rejected else [],
                }
            ]

        selection = {
            ROOT.name: {
                "path": ROOT / "solution/config.yaml",
                "sha256": sha256(ROOT / "solution/config.yaml"),
                "cases": {**json.loads((ROOT / "cases.json").read_text()), "live_cases": list(cases)},
            }
        }

        def experiment(_path, report, body, **_options):
            run.report = report
            body(run)

        real = native_evaluation.reevaluate_native

        def judged(recorded, derived, judgement, **options):
            result = real(recorded, derived, judgement, **options)
            if fault == "changed-acceptance":
                result["acceptance"] = False
            receipts = derived / "judge"
            if fault == "no-receipt":
                shutil.rmtree(receipts)
            elif fault == "deleted-response":
                (receipts / "response.txt").unlink()
            elif fault == "mocked-origin":
                receipt = json.loads((receipts / "receipt.json").read_text())
                write_json(receipts / "receipt.json", {**receipt, "origin": "mocked"})
            elif fault == "other-reservation":
                shutil.rmtree(receipts)
                shutil.copytree(self.root / "earlier/judge", receipts)
            return result

        with (
            patch.dict(os.environ, {"SAPI_WRAPPER_MODEL": RUNTIME_MODEL}),
            patch("sapi_config_lab.coordinate.live.run_experiment", side_effect=experiment),
            patch("sapi_config_lab.coordinate.live.load_selection", return_value=selection),
            patch(
                "sapi_config_lab.coordinate.live.validate_control",
                return_value={"oracle": {"trials": [{"task_name": ROOT.name}]}},
            ),
            patch("sapi_config_lab.coordinate.live.collect_native", side_effect=collected),
            patch(
                "sapi_config_lab.coordinate.live.reconcile_dispatches",
                return_value=[{}] * (1 if fault == "candidate-rejected" else 3),
            ),
            patch("sapi_config_lab.coordinate.native_evaluation.reevaluate_native", side_effect=judged),
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            arguments = ["--stub-report", str(self.control), "--wrapper-evidence", str(self.runtime)]
            arguments += ["--upstream", self.RUNTIME_UPSTREAM, "--report-dir", str(output)]
            arguments += ["--max-calls", str(max_calls), *judge_arguments]
            if cases:
                manifest = self.root / "selection.json"
                manifest.write_text("{}")
                arguments += ["--submissions-manifest", str(manifest)]
            else:
                arguments += ["--scenario", ROOT.name]
            run.exit_code = live_main(arguments)
        return run, output

    def judge_arguments(self, path: str = "/ok") -> list[str]:
        return ["--judge-model", JUDGE_MODEL, *self.wrapper.arguments(path)[1:]]

    def test_runtime_then_one_host_proven_fresh_judge_with_distinct_identities(self):
        run, output = self.live(self.judge_arguments())
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(len(self.wrapper.prompts), 1)
        events = json.loads((output / "ledger.json").read_text())["events"]
        self.assertEqual([(e["phase"], e["status"]) for e in events], [("runtime", "passed"), ("judge", "passed")])
        receipt = json.loads((output / "jobs/live/trial/verifier/paid-evaluation/judge/receipt.json").read_text())
        self.assertEqual((receipt["expected_model"], receipt["reservation"]["index"]), (JUDGE_MODEL, 1))

    def test_reservations_are_exactly_the_reported_per_case_allocation(self):
        for cases, max_calls in (((), 4), (("orion-clinics", "beacon-bookings"), 8)):
            with self.subTest(cases=cases):
                shutil.rmtree(self.root / "run", ignore_errors=True)
                self.wrapper.prompts.clear()
                run, output = self.live(self.judge_arguments(), cases=cases, max_calls=max_calls)
                self.assertEqual(run.exit_code, 0)
                budget = run.report["budget"]
                reserved = {
                    (event["phase"], event["name"].split("/", 1)[1].removesuffix("/judge")): event["count"]
                    for event in json.loads((output / "ledger.json").read_text())["events"]
                }
                self.assertEqual(
                    reserved,
                    {("runtime", name): count for name, count in budget["cases"].items()}
                    | {("judge", name): count for name, count in budget["judge"].items()},
                )
                # V1 selection is Orion alone; a second case exists only when named explicitly.
                expected = {f"{ROOT.name}/{case}": 3 for case in cases or ("orion-clinics",)}
                self.assertEqual((budget["cases"], budget["total"]), (expected, max_calls))
                self.assertEqual(len(self.wrapper.prompts), len(expected))

    def test_preflight_validates_the_judge_but_reserves_and_calls_nothing(self):
        run, output = self.live([*self.judge_arguments(), "--preflight-only"])
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(run.harbor.call_count, 1)
        self.assertEqual(self.wrapper.prompts, [])
        self.assertEqual(json.loads((output / "ledger.json").read_text())["events"], [])

    def test_judge_configuration_is_refused_before_any_runtime_reservation(self):
        runtime_as_judge = self.judge_arguments()
        runtime_as_judge[runtime_as_judge.index("--judge-wrapper-evidence") + 1] = str(self.runtime)
        # Even an inspection that claims the runtime URL for the Judge model cannot share the runtime wrapper.
        same_upstream = self.judge_arguments()
        same_upstream[same_upstream.index("--judge-upstream") + 1] = self.RUNTIME_UPSTREAM
        claimed = self.root / "judge-claims-runtime-url.json"
        claimed.write_text(
            json.dumps({**json.loads(self.wrapper.inspection().read_text()), "endpoint": self.RUNTIME_UPSTREAM})
        )
        same_upstream[same_upstream.index("--judge-wrapper-evidence") + 1] = str(claimed)
        same_model = ["--judge-model", RUNTIME_MODEL, *self.wrapper.arguments(model=RUNTIME_MODEL)[1:]]
        cases = {
            "runtime-inspection": runtime_as_judge,
            "runtime-upstream": same_upstream,
            "runtime-model": same_model,
            "no-endpoint": ["--judge-model", JUDGE_MODEL],
        }
        for name, arguments in cases.items():
            with self.subTest(name=name):
                shutil.rmtree(self.root / "run", ignore_errors=True)
                with self.assertRaises(SystemExit):
                    self.live(arguments)
                self.assertFalse((self.root / "run/ledger.json").exists())
                self.assertEqual(self.wrapper.prompts, [])

    def test_a_missing_substituted_or_foreign_receipt_never_passes_the_judge_stage(self):
        earlier = fixture_record(self.root / "earlier-record", judge_mode="wrapper", judge_model=JUDGE_MODEL)
        code, _printed = evaluate_cli(
            "--record",
            str(earlier),
            "--output",
            str(self.root / "earlier"),
            "--judge-model",
            JUDGE_MODEL,
            *self.wrapper.arguments(),
        )
        self.assertEqual(code, 0)
        for fault in ("no-receipt", "deleted-response", "mocked-origin", "other-reservation"):
            with self.subTest(fault=fault):
                shutil.rmtree(self.root / "run", ignore_errors=True)
                with self.assertRaises(SystemExit):
                    self.live(self.judge_arguments(), fault)
                events = json.loads((self.root / "run/ledger.json").read_text())["events"]
                self.assertEqual(
                    [(e["phase"], e["status"]) for e in events], [("runtime", "passed"), ("judge", "failed")]
                )

    def test_rejected_case_below_runtime_minimum_has_null_quality_and_no_judge_event(self):
        run, output = self.live(self.judge_arguments(), "candidate-rejected")
        self.assertEqual(run.exit_code, 0)
        self.assertEqual(self.wrapper.prompts, [])
        events = json.loads((output / "ledger.json").read_text())["events"]
        self.assertEqual(
            [(event["phase"], event["count"], event["status"]) for event in events], [("runtime", 3, "passed")]
        )
        self.assertEqual(len(run.report["correlation"]), 1)
        self.assertEqual(run.report["case_outcomes"], {"research-report/orion-clinics": "rejected"})
        trial = run.report["trials"][0]
        self.assertIsNone(trial["result"]["quality"])
        self.assertIsNone(trial["exception"])
        self.assertEqual(trial["native_exception"], {"exception_type": "RewardFileNotFoundError"})
        self.assertFalse((output / "jobs/live/trial/verifier/paid-evaluation").exists())

    def test_judge_cannot_substitute_a_rejection_for_the_runtime_accepted_verdict(self):
        with self.assertRaises(SystemExit):
            self.live(self.judge_arguments(), "changed-acceptance")
        events = json.loads((self.root / "run/ledger.json").read_text())["events"]
        self.assertEqual(
            [(event["phase"], event["status"]) for event in events], [("runtime", "passed"), ("judge", "failed")]
        )
