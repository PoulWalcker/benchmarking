"""Unpaid guards for the live replay runner; no Docker or real wrapper calls."""

import contextlib
import copy
from functools import partial
import hashlib
import io
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.coordinate.live import audit_records, check_trials, main, validate_control
from sapi_config_lab.coordinate.live_evidence import reconcile_dispatches
from sapi_config_lab.coordinate.progress import tracking
from sapi_config_lab.coordinate.replay import load_selection, read_json
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.coordinate.wrapper import parse_wrapper_files, wrapper_identity
from sapi_config_lab.evidence import digest, sha256
from sapi_config_lab.execute.agency import DispatchAudit, start_bridge
from sapi_config_lab.execute.agency import execute as agency_execute
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read_bindings
from tests.support.invoice import CATALOG

execute = partial(agency_execute, transport=request_wrapper)


class LiveEvidenceTests(unittest.TestCase):
    def test_dispatch_response_and_native_records_must_all_agree(self):
        request = {
            "invocation_id": "wf/step/1",
            "operation": "ticket.classify",
            "actor": None,
            "inputs": {"ticket": {"id": "T1", "text": "late", "days_overdue": 3}},
        }
        output = {"category": "delivery", "priority": "high"}
        response = {"invocation_id": request["invocation_id"], "status": "completed", "output": output, "usage": None}
        native = [{"request": request, "response": response}]
        with tempfile.TemporaryDirectory() as directory:
            budget = {"max_attempts": 1, "operations": {"ticket.classify": 1}, "model": "gpt-6-astra"}
            audit = DispatchAudit(Path(directory) / "audit.jsonl", budget)
            wrapper = {
                "ok": True,
                "exit_code": 0,
                "output": json.dumps(output),
                "stderr": "model: gpt-6-astra\ntokens used\n14\n",
            }
            with patch(
                "sapi_config_lab.harbor_integration.model_wrapper.urlopen",
                return_value=BytesIO(json.dumps(wrapper).encode()),
            ):
                execute(request, read_bindings(CATALOG), "http://unused", 1, audit=audit)
            audit.append(
                {
                    "event": "agency_response",
                    "invocation_id": request["invocation_id"],
                    "operation": request["operation"],
                    "inputs_sha256": digest(request["inputs"]),
                    "http_status": 200,
                    "status": "completed",
                    "model": "gpt-6-astra",
                }
            )
            records = audit.records()
            self.assertEqual(len(reconcile_dispatches(native, records, "gpt-6-astra", bindings=CATALOG)), 1)
            catalog = read_bindings(CATALOG)
            catalog["ticket.classify"]["prompt"] += "\nScenario-specific instruction."
            selected = Path(directory) / "bindings.yaml"
            selected.write_text(yaml.safe_dump({"operations": catalog}, sort_keys=False))
            from sapi_config_lab.execute.agency import build_prompt

            changed = copy.deepcopy(records)
            for row in changed[:2]:
                row["prompt_sha256"] = hashlib.sha256(
                    build_prompt(request, catalog["ticket.classify"]).encode()
                ).hexdigest()
            self.assertEqual(len(reconcile_dispatches(native, changed, "gpt-6-astra", bindings=selected)), 1)
            with self.assertRaisesRegex(ValueError, "prompt identity"):
                reconcile_dispatches(native, changed, "gpt-6-astra", bindings=CATALOG)

            for corrupt in (
                "orphan_completion",
                "duplicate",
                "response_hash",
                "input_hash",
                "model",
                "sequence",
                "unknown_id",
                "forged_success",
            ):
                bad = copy.deepcopy(records)
                if corrupt == "orphan_completion":
                    bad.pop(0)
                elif corrupt == "duplicate":
                    bad += copy.deepcopy(records)
                elif corrupt == "sequence":
                    bad[0], bad[1] = bad[1], bad[0]
                elif corrupt == "model":
                    bad[1]["wrapper"]["model"] = "fake"
                elif corrupt == "unknown_id":
                    bad[1]["invocation_id"] = "unmatched"
                elif corrupt == "forged_success":
                    bad[1]["wrapper"]["ok"] = False
                else:
                    bad[1]["response_sha256" if corrupt == "response_hash" else "inputs_sha256"] = "0" * 64
                with self.subTest(corrupt=corrupt), self.assertRaises(ValueError):
                    reconcile_dispatches(native, bad, "gpt-6-astra", bindings=CATALOG)

    def test_source_and_image_gate_rejects_success_from_other_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.json").write_text(json.dumps({"src/example.py": "abc"}))
            gate = {
                "schema": "sapi-lab-harbor/v1",
                "status": "passed",
                "mode": "stub",
                "source_unchanged": True,
                "source_manifest": "source.json",
                "image_id": "sha256:abc",
                "transport": {"passed": True},
                "oracle": {"passed": True},
                "nop": {"passed": True},
                "checks": [{"passed": True}],
            }
            path = root / "report.json"
            path.write_text(json.dumps(gate))
            validate_control(path, {"src/example.py": "abc"}, "sha256:abc")
            for sources, image in (
                ({"src/example.py": "edited"}, "sha256:abc"),
                ({"src/example.py": "abc"}, "sha256:changed"),
            ):
                with self.assertRaises(ValueError):
                    validate_control(path, sources, image)
            for field in ("source_unchanged", "transport", "oracle", "nop"):
                path.write_text(
                    json.dumps({**gate, field: False if field == "source_unchanged" else {"passed": False}})
                )
                with self.assertRaises(ValueError):
                    validate_control(path, {"src/example.py": "abc"}, "sha256:abc")

    def test_missing_or_tampered_selection_and_existing_output_never_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "selection.json"
            with patch("sapi_config_lab.execute.agency.subprocess.Popen") as dispatch:
                for manifest in (
                    {},
                    {"schema": "fake", "entries": []},
                    {"source_report": {"path": str(root / "absent")}},
                ):
                    path.write_text(json.dumps(manifest))
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        load_selection(path)
                for argv, message in (
                    (["--report-dir", str(root), "--preflight-only"], "never overwritten"),
                    (["--report-dir", str(root / "new"), "--max-calls", "8"], "requires --wrapper-evidence"),
                ):
                    with contextlib.redirect_stderr(io.StringIO()) as errors:
                        with self.assertRaises(SystemExit) as caught:
                            main(["--stub-report", str(root / "absent"), "--max-calls", "8", *argv])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertIn(message, errors.getvalue())
                self.assertFalse((root / "new").exists())
                dispatch.assert_not_called()

    def test_truncated_audit_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            for invalid in ('{"event":"completion"', "null\n", "[]\n", "42\n"):
                path.write_text(invalid)
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    audit_records(path)

    def test_canonical_input_hash_is_key_order_independent_and_rejects_nonfinite(self):
        self.assertEqual(digest({"b": [2], "a": "é"}), digest({"a": "é", "b": [2]}))
        with self.assertRaises(ValueError):
            digest({"x": float("nan")})


class HostedTrialCheckTests(unittest.TestCase):
    """A hosted live trial must be evaluated; it must be scored only where a reference reward is declared."""

    def check(self, result: dict, reference_reward: float | None) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        scenario = Path(temporary.name) / "checkout-recovery"
        scenario.mkdir()
        (scenario / "task.toml").write_text(
            '[metadata.sapi]\nadmission = "compile"\n'
            + (f"reference_reward = {reference_reward}\n" if reference_reward is not None else "")
        )
        acceptance = {
            "schema": "sapi-lab-upstream-acceptance/v1",
            "mode": "live",
            "scenario": "checkout-recovery",
            "submission_sha256": "abc",
            "result": result,
        }
        trial = {"task_name": "checkout-recovery", "exception": None, "acceptance": acceptance, "result": result}
        check_trials(
            [trial], {"checkout-recovery": {"sha256": "abc"}}, benchmarks={scenario.name: scenario}, mode="live"
        )

    def test_an_unscored_trial_is_complete_only_without_a_reference_reward(self):
        scored = {"status": "complete", "score_0_10": 0.0, "normalized_reward": 0.0}
        self.check({"execution": True, "acceptance": False, "quality": scored}, 0.732)
        self.check({"execution": True, "acceptance": False, "quality": None}, None)
        with self.assertRaisesRegex(ValueError, "unscored"):
            self.check({"execution": True, "acceptance": True, "quality": None}, 0.732)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.check({"execution": None, "acceptance": None, "quality": None}, None)


class WrapperIdentityTests(unittest.TestCase):
    def identity(self, root: Path, files: list[dict]) -> Path:
        path = root / "identity.json"
        record = {
            "schema": "sapi-lab-wrapper-identity/v1",
            "endpoint": "http://127.0.0.1:8765/run",
            "dispatch": "codex-exec",
            "response_substitution": False,
            "wrapper_retries": 0,
            "provider_internal_retries": "unknown",
            "model": "gpt-6-astra",
            "files": files,
        }
        path.write_text(json.dumps(record))
        return path

    def test_a_relocated_wrapper_is_named_explicitly_and_still_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            moved = root / "elsewhere/codex_bridge.py"
            moved.parent.mkdir()
            moved.write_text("wrapper source")
            recorded = {"path": "/Users/someone/n8n/codex_bridge.py", "sha256": sha256(moved)}
            path = self.identity(root, [recorded])

            def check(files=None):
                return wrapper_identity(path, "http://127.0.0.1:8765/run", "gpt-6-astra", files)

            with self.assertRaisesRegex(ValueError, "identity changed"):
                check()
            self.assertEqual(check({"codex_bridge.py": moved})["model"], "gpt-6-astra")
            with self.assertRaisesRegex(ValueError, "does not record"):
                check({"other.py": moved})
            moved.write_text("edited wrapper source")
            with self.assertRaisesRegex(ValueError, "identity changed"):
                check({"codex_bridge.py": moved})
            with self.assertRaisesRegex(ValueError, "incompatible"):
                wrapper_identity(path, "http://127.0.0.1:8765/run", "other-model", {"codex_bridge.py": moved})

    def test_wrapper_file_arguments_are_explicit_pairs(self):
        self.assertEqual(parse_wrapper_files(["a.py=/x/a.py"]), {"a.py": Path("/x/a.py")})
        for bad in (["a.py"], ["=/x"], ["a.py=/x", "a.py=/y"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_wrapper_files(bad)


class HostConfigTests(unittest.TestCase):
    def test_every_machine_setting_has_an_environment_override(self):
        defaults = HostConfig()
        configured = HostConfig.from_environment({"SAPI_CONTAINER_HOST": "172.17.0.1", "SAPI_BRIDGE_PORT": "19000"})
        self.assertEqual(configured.container_host, "172.17.0.1")
        self.assertEqual(configured.bridge_port, 19000)
        self.assertEqual(configured.wrapper_url, defaults.wrapper_url)
        self.assertEqual(HostConfig.from_environment({}), defaults)


class BridgeBindingTests(unittest.TestCase):
    def test_the_bridge_listens_where_containers_are_routed(self):
        host = HostConfig.from_environment({"SAPI_CONTAINER_HOST": "172.17.0.1", "SAPI_LISTEN_HOST": "0.0.0.0"})
        self.assertEqual(host.container_url(host.bridge_port), "http://172.17.0.1:18765")
        root = Path(tempfile.mkdtemp())
        run = Run(root / "run", {}, {}, "t", host)
        run.output.mkdir()
        with (
            patch("sapi_config_lab.coordinate.runs.start_bridge") as start,
            patch("sapi_config_lab.coordinate.runs.stop_bridge"),
            run.bridge({"max_attempts": 0}, "case", bindings=CATALOG),
        ):
            pass
        self.assertEqual(start.call_args.args[:3], ("0.0.0.0", 18765, host.wrapper_url))

    def test_the_bridge_process_binds_and_probes_the_configured_address(self):
        for listen, probed in (("0.0.0.0", "127.0.0.1"), ("172.17.0.1", "172.17.0.1"), ("127.0.0.1", "127.0.0.1")):
            with (
                self.subTest(listen=listen),
                tempfile.TemporaryDirectory() as directory,
                patch("sapi_config_lab.execute.agency.subprocess.Popen") as popen,
                patch("sapi_config_lab.execute.agency.urlopen") as urlopen,
                patch("sapi_config_lab.execute.agency.time.sleep"),
            ):
                popen.return_value.poll.return_value = None
                urlopen.return_value.__enter__.return_value = BytesIO(b'{"service": "sapi-lab-agency-adapter"}')
                root = Path(directory)
                start_bridge(listen, 18765, "http://wrapper", root / "a", root / "b", root / "log", bindings=CATALOG)
                argv = popen.call_args.args[0]
                self.assertEqual(argv[argv.index("--host") + 1], listen)
                self.assertEqual(urlopen.call_args.args[0], f"http://{probed}:18765/health")

    def test_loopback_stays_the_default(self):
        self.assertEqual(HostConfig().listen_host, "127.0.0.1")


class NativeLiveReservationTests(unittest.TestCase):
    def fixture_dispatch(self, root: Path, derived: Path, fault: str, reservation: dict) -> None:
        """What a fixture task's child leaves behind: a real fresh receipt, then the fault's substitution."""
        import shutil

        from sapi_config_lab.coordinate.fixture_judge import FixtureJudge
        from sapi_config_lab.coordinate.native_evaluation import evaluate_record
        from tests.test_fixture_judge import (
            CARD,
            MODEL,
            REQUEST,
            answer,
            fresh_factory,
            judge_world,
            rubric_evaluator,
            valid_wrapper,
        )

        record, inspection = judge_world(root / "world")
        request = {"record": str(record), "output": str(derived), "dispatch": True}
        if fault != "receipt-standalone":
            request["reservation"] = reservation
        task = workspace_root() / "tasks/checkout-recovery"
        evaluate_record(task, rubric_evaluator, request, judge_factory=fresh_factory(inspection, valid_wrapper))
        if fault == "receipt-mocked":
            mocked = root / "world/mocked"
            FixtureJudge.mock(mocked, (record / "native-task.json").read_bytes(), CARD, MODEL, answer).judge(REQUEST)
            shutil.rmtree(derived / "judge")
            shutil.copytree(mocked, derived / "judge")

    def test_runtime_closes_before_judge_and_invalid_trials_never_dispatch_judge(self):
        from unittest.mock import Mock

        from sapi_config_lab.coordinate.native_tasks import policy as task_policy
        from sapi_config_lab.evidence import write_json

        scenario = workspace_root() / "tasks/checkout-recovery"
        submission_hash = sha256(scenario / "solution/config.yaml")
        faults = (None, "duplicate", "submission", "runtime-timeout", "judge-incomplete", "judge-postcondition")
        receipts = ("receipt-fresh", "receipt-standalone", "receipt-mocked")
        for fault in faults + receipts:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "run"
                output.mkdir()
                control, wrapper = root / "control.json", root / "wrapper.json"
                control.write_text("{}")
                wrapper.write_text("{}")
                record = output / "jobs/live/trial/verifier"
                record.mkdir(parents=True)
                quality = {"status": "complete", "score_0_10": 0.0, "normalized_reward": 0.0}
                result = {"execution": True, "acceptance": False, "quality": quality}

                def trial(mode, admission=False, *, record=record, result=result):
                    return {
                        "task_name": scenario.name,
                        "result_path": str(record.parent / "result.json"),
                        "exception": None,
                        "rewards": {"reward": 1.0},
                        "result": {"execution": None, "acceptance": None, "quality": None} if admission else result,
                        "acceptance": {
                            "schema": "sapi-lab-admission/v1" if admission else "sapi-lab-upstream-acceptance/v1",
                            "mode": mode,
                            "scenario": scenario.name,
                            "passed": True,
                            "submission_sha256": submission_hash,
                        },
                    }

                run = Mock()
                run.step.side_effect = lambda *args, **kwargs: contextlib.nullcontext()
                run.output, run.sources, run.tasks, run.image = output, {}, output / "tasks", "frozen:image"
                run.use_image.return_value = "sha256:stub"
                run.bridge.return_value = contextlib.nullcontext(output / "audit.jsonl")
                live_trial = trial("live")
                if fault == "runtime-timeout":
                    audit = DispatchAudit(
                        output / "audit.jsonl",
                        {"max_attempts": 1, "operations": {"SummarizeIncident": 1}, "model": "mocked-only"},
                    )
                    audit.fail({}, "timeout_unknown_outcome", outcome="unknown")
                if fault == "submission":
                    live_trial["acceptance"]["submission_sha256"] = "0" * 64
                run.harbor.side_effect = [
                    (0, [trial("stub", True)]),
                    (0, [live_trial] * (2 if fault == "duplicate" else 1)),
                ]

                display = io.StringIO()

                def experiment(path, report, body, run=run, display=display, **options):
                    self.assertEqual((options["command"], options["stages"]), ("sapi-lab live", ["preflight"]))
                    with tracking("sapi-lab live", options["stages"], stream=display, refresh=None):
                        body(run)

                def judge(
                    recorded,
                    derived,
                    judgement,
                    output=output,
                    live_trial=live_trial,
                    result=result,
                    fault=fault,
                    root=root,
                    **options,
                ):
                    events = read_json(output / "ledger.json")["events"]
                    self.assertEqual(
                        [(event["phase"], event["status"]) for event in events],
                        [("runtime", "passed"), ("judge", "unknown")],
                    )
                    if fault in receipts:
                        self.fixture_dispatch(root, derived, fault, options["reservation"])
                    (derived / "evaluation").mkdir(parents=True, exist_ok=True)
                    judged = {**result, "quality": None} if fault == "judge-incomplete" else result
                    write_json(derived / "evaluation/report.json", {**live_trial["acceptance"], "result": judged})
                    if fault == "judge-postcondition":
                        write_json(derived / "result.json", judged)
                        raise ValueError("Judge dispatch incomplete: callback was not consumed")
                    return judged

                with (
                    patch("sapi_config_lab.coordinate.live.run_experiment", side_effect=experiment),
                    patch(
                        "sapi_config_lab.coordinate.live.validate_control",
                        return_value={"oracle": {"trials": [{"task_name": scenario.name}]}},
                    ),
                    patch("sapi_config_lab.coordinate.live.wrapper_identity", return_value={"model": "gpt-6-astra"}),
                    patch("sapi_config_lab.coordinate.live.collect_native", return_value=[]),
                    patch("sapi_config_lab.coordinate.live.reconcile_dispatches", return_value=[]),
                    patch(
                        "sapi_config_lab.coordinate.live.policy",
                        side_effect=lambda task, fault=fault: {
                            **task_policy(task),
                            **({"reference_reward": None} if fault == "judge-incomplete" else {}),
                        },
                    ),
                    patch(
                        "sapi_config_lab.coordinate.native_evaluation.reevaluate_native", side_effect=judge
                    ) as dispatch,
                    contextlib.redirect_stderr(io.StringIO()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    arguments = [
                        "--stub-report",
                        str(control),
                        "--wrapper-evidence",
                        str(wrapper),
                        "--report-dir",
                        str(output),
                        "--max-calls",
                        "5",
                        "--scenario",
                        scenario.name,
                        "--judge-model",
                        "stub-judge",
                    ]
                    case = f"case 1/1 {scenario.name}/"
                    if fault in (None, "receipt-fresh"):
                        self.assertEqual(main(arguments), 0)
                        dispatch.assert_called_once()
                        lines = display.getvalue().splitlines()
                        # Cases become known stages only after preflight admitted them.
                        self.assertIn("[sapi-lab live] 1/1 preflight · done <1s", lines)
                        self.assertTrue(any(line.startswith(f"[sapi-lab live] 2/2 {case}") for line in lines))
                        self.assertRegex(display.getvalue(), r"\] 2/2 case 1/1 [^\n]+ · done")
                        self.assertIn(f"[1/1] {scenario.name}/workflow: live, up to 0 model calls reserved", lines)
                        self.assertIn("[sapi-lab live]   Judge: 1 reserved call; task evaluator running", lines)
                        self.assertTrue(
                            all(event["status"] == "passed" for event in read_json(output / "ledger.json")["events"])
                        )
                    elif fault == "runtime-timeout":
                        import subprocess

                        with self.assertRaises(subprocess.TimeoutExpired):
                            main(arguments)
                        dispatch.assert_not_called()
                        self.assertRegex(display.getvalue(), r"\] 2/2 case 1/1 [^\n]+ · unknown")
                        self.assertNotIn("Judge:", display.getvalue())
                        events = read_json(output / "ledger.json")["events"]
                        self.assertEqual(
                            [(event["phase"], event["status"]) for event in events], [("runtime", "unknown")]
                        )
                    elif fault == "judge-incomplete":
                        with self.assertRaises(SystemExit):
                            main(arguments)
                        self.assertEqual(read_json(output / "ledger.json")["events"][-1]["status"], "failed")
                        self.assertEqual(live_trial["result"]["acceptance"], False)
                        self.assertIsNone(live_trial["result"]["quality"])
                    elif fault in ("judge-postcondition", "receipt-standalone", "receipt-mocked"):
                        with self.assertRaises(SystemExit):
                            main(arguments)
                        self.assertEqual(read_json(output / "ledger.json")["events"][-1]["status"], "failed")
                        self.assertEqual(live_trial["result"], result)
                        self.assertEqual(live_trial["acceptance"]["result"], result)
                    else:
                        with self.assertRaises(SystemExit):
                            main(arguments)
                        dispatch.assert_not_called()


class LiveFixtureCohortTests(unittest.TestCase):
    """A fake native dispatcher leaves settled audit evidence for the live CLI."""

    def replay(self, outcomes, *, fault=None, latch=False):
        from unittest.mock import Mock

        from sapi_config_lab.execute.agency import build_prompt
        from sapi_config_lab.profile import read
        from tests.test_n8n_evidence import NativeLiveEvidenceTests
        from verification.n8n_provenance import live_operations

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        output = root / "run"
        output.mkdir()
        task = workspace_root() / "tasks/ticket-routing"
        cases = json.loads((task / "cases.json").read_text())
        names = [row["name"] for row in cases["positive"][: len(outcomes)]]
        cases["live_cases"] = names
        path = root / "submission.yaml"
        config = read(task / "solution/config.yaml")
        path.write_text(yaml.safe_dump(config))
        selected = {task.name: {"path": path, "sha256": sha256(path), "cases": cases}}
        control, wrapper, manifest = root / "control.json", root / "wrapper.json", root / "selection.json"
        for file in (control, wrapper, manifest):
            file.write_text("{}")
        report_holder = {}
        native_cases, live_trials, audits = [], [], []
        for index, (name, outcome) in enumerate(zip(names, outcomes, strict=True)):
            verifier = output / f"jobs/{index}/trial/verifier"
            (verifier / "evidence").mkdir(parents=True)
            (verifier / "evidence/submission.yaml").write_bytes(path.read_bytes())
            (verifier / "evidence/observation.json").write_text(json.dumps({"entries": [{"name": name}]}))
            accepted = outcome == "accepted"
            trial = {
                "task_name": task.name,
                "result_path": str(verifier.parent / "result.json"),
                "verdict_path": str(verifier / "result.json"),
                "exception": None,
                "rewards": {"reward": float(accepted)},
                "result": {"execution": outcome in {"accepted", "rejected"}, "acceptance": accepted, "quality": None},
                "acceptance": {
                    "schema": "sapi-lab-acceptance/v1",
                    "mode": "live",
                    "scenario": task.name,
                    "submission_sha256": sha256(path),
                    "cases": [{"name": name, "passed": accepted}],
                },
            }
            run = NativeLiveEvidenceTests().live_record()
            calls = live_operations(run)
            record = {
                "status": "error",
                "persisted_status": "error",
                "result_node_present": False,
                "mapping": {"select": "select", "classify": "classify [LLM LIVE]"},
                "run_data": {
                    "select": [
                        {
                            "startTime": 50,
                            "error": {
                                "name": "WrappedExecutionError",
                                "message": "Exactly one routing branch must complete [line 13]",
                            },
                        }
                    ]
                },
            }
            trace = "complete" if outcome in {"accepted", "rejected"} else "incomplete"
            if outcome == "prepare-rejected":
                record["run_data"] = {
                    "Prepare classify": [
                        {
                            "startTime": 0,
                            "error": {
                                "name": "WrappedExecutionError",
                                "message": "Schema violation at inputs.ticket: missing id [line 65]",
                            },
                        }
                    ]
                }
                calls = []
            elif outcome == "deadline":
                record["run_data"]["select"][0]["error"]["message"] = "Workflow deadline exceeded [line 76]"
            elif outcome == "agency":
                record["run_data"] = {
                    "Agency classify": [
                        {
                            "startTime": 0,
                            "error": {
                                "name": "NodeApiError",
                                "message": "Connection refused",
                                "httpCode": "ECONNREFUSED",
                            },
                        }
                    ]
                }
            if outcome in {"engine_error", "import_error", "compile_error"}:
                record["status"] = outcome
            elif outcome == "missing-persisted":
                record.pop("persisted_status")
            elif outcome in {"restore", "fixture"}:
                node = "classify [LLM LIVE]" if outcome == "restore" else "Fixture"
                record["run_data"] = {
                    node: [
                        {
                            "startTime": 10,
                            "error": {"name": "WrappedExecutionError", "message": "Invalid Agency response [line 118]"},
                        }
                    ]
                }
            elif outcome in {"type-error", "other-error"}:
                record["run_data"]["select"][0]["error"] = {
                    "name": "TypeError" if outcome == "type-error" else "Error",
                    "message": "Exactly one routing branch must complete",
                }
            elif outcome == "multiple-errors":
                record["run_data"]["Prepare classify"] = copy.deepcopy(record["run_data"]["select"])
            elif outcome == "canceled":
                record["persisted_status"] = "canceled"
                record["run_data"] = {"Fixture": [{"startTime": 0}]}
            elif outcome == "incomplete-accepted":
                trial["result"]["acceptance"] = True
            graph = {"nodes": [{"name": node, "type": "n8n-nodes-base.code"} for node in record["run_data"]]}
            native_cases.append(
                {
                    "scenario": task.name,
                    "case": name,
                    "trace": trace,
                    "calls": calls,
                    "status": record["status"],
                    "record": record,
                    "graph": graph,
                    "failed_node": [{"node": node} for node in record["run_data"]] if trace == "incomplete" else [],
                }
            )
            rows = []
            for count, call in enumerate(calls, 1):
                request, response = call["request"], call["response"]
                common = {
                    "schema": "sapi-lab-dispatch/v1",
                    "attempt": count,
                    "invocation_id": request["invocation_id"],
                    "operation": request["operation"],
                    "inputs_sha256": digest(request["inputs"]),
                    "request_sha256": digest(request),
                    "prompt_sha256": hashlib.sha256(
                        build_prompt(request, read_bindings(task / "bindings.yaml")[request["operation"]]).encode()
                    ).hexdigest(),
                }
                rows.extend(
                    [
                        {**common, "event": "dispatch_attempt", "timestamp_unix": 1},
                        {
                            **common,
                            "event": "completion",
                            "timestamp_unix": 2,
                            "response_sha256": digest(response),
                            "output_sha256": digest(response["output"]),
                            "wrapper": {
                                "ok": True,
                                "exit_code": 0,
                                "model": "gpt-6-astra",
                                "response_bytes": 1,
                                "response_sha256": "a" * 64,
                                "output_text_sha256": "b" * 64,
                                "stderr_sha256": "c" * 64,
                            },
                        },
                        {
                            **common,
                            "event": "agency_response",
                            "timestamp_unix": 3,
                            "status": "completed",
                            "http_status": 200,
                            "model": "gpt-6-astra",
                        },
                    ]
                )
            if fault and index == len(outcomes) - 1:
                if fault == "submission":
                    trial["acceptance"]["submission_sha256"] = "0" * 64
                elif fault in {"mode", "scenario"}:
                    trial["acceptance"][fault] = "wrong"
                elif fault == "cases":
                    (verifier / "evidence/observation.json").write_text(json.dumps({"entries": [{"name": "wrong"}]}))
                elif fault == "exception":
                    trial["exception"] = {"exception_type": "VerifierTimeoutError"}
                elif fault == "null":
                    trial["result"]["acceptance"] = None
                elif fault == "verdict":
                    trial["result"] = None
                elif fault == "unbound":
                    rows = []
                elif fault == "extra":
                    rows += copy.deepcopy(rows)
                elif fault == "audit-failure":
                    rows.append({"event": "failure", "category": "transport_error", "outcome": "failed"})
            live_trials.append(trial)
            audits.append(rows)
        stub = copy.deepcopy(live_trials[0])
        stub["exception"] = None
        stub["acceptance"].update(mode="stub", passed=True, cases=[{"name": name, "passed": True} for name in names])
        stub["result"] = {"execution": True, "acceptance": True, "quality": None}
        dispatched = []
        run = Mock()
        run.output, run.sources = output, {}
        run.use_image.return_value = "sha256:stub"
        run.step.side_effect = lambda *args, **kwargs: contextlib.nullcontext()

        def harbor(label, *_args, **_kwargs):
            if label.startswith("stub"):
                if fault == "stub":
                    stub["result"]["acceptance"] = False
                return 0, [stub]
            index = len(dispatched)
            dispatched.append(names[index])
            audit = output / f"audit-{index}.jsonl"
            audit.write_text("".join(json.dumps(row) + "\n" for row in audits[index]))
            return (1 if fault == "exit" and index == len(outcomes) - 1 else 0), [live_trials[index]]

        run.harbor.side_effect = harbor
        run.bridge.side_effect = lambda *_args, **_kwargs: contextlib.nullcontext(
            output / f"audit-{len(dispatched)}.jsonl"
        )
        stderr, stdout = io.StringIO(), io.StringIO()

        def experiment(_path, report, body, **options):
            report_holder.update(report=report)
            with tracking("sapi-lab live", ["preflight"], stream=stderr, refresh=None):
                try:
                    body(run)
                except ValueError as error:
                    report.update(status="failed", failure_category=options["classify"](error), error=str(error))

        with (
            patch.dict("os.environ", {"SAPI_WRAPPER_MODEL": "gpt-6-astra"}),
            patch("sapi_config_lab.coordinate.live.run_experiment", side_effect=experiment),
            patch(
                "sapi_config_lab.coordinate.live.validate_control",
                return_value={"oracle": {"trials": [{"task_name": task.name}]}},
            ),
            patch("sapi_config_lab.coordinate.live.wrapper_identity", return_value={"model": "gpt-6-astra"}),
            patch("sapi_config_lab.coordinate.live.load_selection", return_value=selected),
            patch("sapi_config_lab.coordinate.live.collect_native", side_effect=[[row] for row in native_cases]),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "--stub-report",
                    str(control),
                    "--wrapper-evidence",
                    str(wrapper),
                    "--submissions-manifest",
                    str(manifest),
                    "--report-dir",
                    str(output),
                    "--max-calls",
                    str(len(outcomes)),
                    *(["--stop-after-failure"] if latch else []),
                ]
            )
        return (
            code,
            report_holder["report"],
            read_json(output / "ledger.json")["events"],
            stderr.getvalue(),
            stdout.getvalue(),
            dispatched,
        )

    def test_complete_rejection_is_measured_and_does_not_latch_the_cohort(self):
        code, report, events, stderr, stdout, dispatched = self.replay(["rejected", "accepted"], latch=True)
        self.assertEqual(code, 0)
        self.assertEqual(report["not_run"], [])
        self.assertEqual(list(report["case_outcomes"].values()), ["rejected", "accepted"])
        self.assertEqual(report["acceptance_counts"], {"accepted": 1, "rejected": 1})
        self.assertEqual([event["status"] for event in events], ["passed", "passed"])
        self.assertEqual(len(dispatched), 2)
        self.assertIn("high-priority: rejected", stderr)
        self.assertIn("normal-priority: accepted", stderr)
        self.assertIn("live: 1 accepted, 1 rejected of 2 cases", stderr)
        self.assertEqual(set(json.loads(stdout)), {"status", "report"})

    def test_complete_and_incomplete_rejections_continue_in_both_orders(self):
        for rejection in ("rejected", "operation-rejected", "prepare-rejected"):
            for outcomes in ([rejection, "accepted"], ["accepted", rejection]):
                with self.subTest(outcomes=outcomes):
                    code, report, events, stderr, _stdout, _dispatched = self.replay(outcomes)
                    self.assertEqual(code, 0)
                    self.assertEqual(report["not_run"], [])
                    self.assertEqual(
                        list(report["case_outcomes"].values()),
                        ["accepted" if row == "accepted" else "rejected" for row in outcomes],
                    )
                    self.assertEqual([event["status"] for event in events], ["passed", "passed"])
                    self.assertEqual(report["acceptance_counts"], {"accepted": 1, "rejected": 1})
                    rejected_key = next(key for key, value in report["case_outcomes"].items() if value == "rejected")
                    attribution = report["case_attribution"][rejected_key]
                    self.assertEqual(attribution["trace"], "complete" if rejection == "rejected" else "incomplete")
                    self.assertIn(": rejected", stderr)

    def test_a_stop_after_rejection_is_attributed_to_its_own_unverified_case(self):
        code, report, events, stderr, _stdout, dispatched = self.replay(["rejected", "deadline", "accepted"])
        self.assertEqual(code, 1)
        self.assertEqual(report["failure_category"], "unverified_live_outcome")
        self.assertEqual(list(report["case_outcomes"].values()), ["rejected", "unverified"])
        self.assertEqual(report["acceptance_counts"], {"accepted": 0, "rejected": 1})
        self.assertEqual(report["not_run"], ["ticket-routing/normal-priority", "ticket-routing/boundary-two-days"])
        self.assertEqual([event["status"] for event in events], ["passed", "failed"])
        self.assertEqual(dispatched, ["high-priority", "normal-priority"])
        self.assertIn("stopped: unverified (deadline)", stderr)

    def test_reconciliation_faults_are_infrastructure_and_stop_after_a_measured_rejection(self):
        for fault in ("unbound", "extra", "audit-failure"):
            with self.subTest(fault=fault):
                code, report, events, stderr, _stdout, dispatched = self.replay(["rejected", "accepted"], fault=fault)
                self.assertEqual(code, 1)
                self.assertEqual(list(report["case_outcomes"].values()), ["rejected", "infrastructure"])
                self.assertEqual(report["acceptance_counts"], {"accepted": 0, "rejected": 1})
                self.assertEqual([event["status"] for event in events], ["passed", "failed"])
                self.assertIn("ticket-routing/normal-priority", report["not_run"])
                self.assertEqual(len(dispatched), 2)
                self.assertIn("stopped: infrastructure", stderr)

    def test_fatal_guards_remain_fatal_after_a_measured_rejection(self):
        for fault in ("submission", "mode", "scenario", "cases", "exit", "exception", "null", "verdict"):
            with self.subTest(fault=fault):
                code, report, events, _stderr, _stdout, dispatched = self.replay(["rejected", "accepted"], fault=fault)
                self.assertEqual(code, 1)
                self.assertEqual(report["acceptance_counts"], {"accepted": 0, "rejected": 1})
                self.assertEqual([event["status"] for event in events], ["passed", "failed"])
                self.assertEqual(report["not_run"], ["ticket-routing/normal-priority"])
                self.assertEqual(len(dispatched), 2)
                self.assertNotEqual(report["failure_category"], "business_acceptance")

    def test_stub_rejection_refuses_before_any_runtime_reservation(self):
        code, report, events, _stderr, _stdout, dispatched = self.replay(["accepted"], fault="stub")
        self.assertEqual(code, 1)
        self.assertEqual(events, [])
        self.assertEqual(dispatched, [])
        self.assertEqual(report["case_outcomes"], {})

    def test_infrastructure_matrix_stops_without_counting_a_rejection(self):
        for outcome in (
            "agency",
            "restore",
            "fixture",
            "engine_error",
            "import_error",
            "compile_error",
            "missing-persisted",
        ):
            with self.subTest(outcome=outcome):
                code, report, events, stderr, _stdout, dispatched = self.replay([outcome, "accepted"])
                self.assertEqual(code, 1)
                self.assertEqual(list(report["case_outcomes"].values()), ["infrastructure"])
                self.assertEqual(report["acceptance_counts"], {"accepted": 0, "rejected": 0})
                self.assertEqual([event["status"] for event in events], ["failed"])
                self.assertEqual(dispatched, ["high-priority"])
                self.assertIn("ticket-routing/normal-priority", report["not_run"])
                self.assertIn("stopped: infrastructure", stderr)

    def test_unverified_matrix_stops_with_a_reason_and_no_false_rejection(self):
        for outcome in ("deadline", "type-error", "other-error", "multiple-errors", "canceled", "incomplete-accepted"):
            with self.subTest(outcome=outcome):
                code, report, events, stderr, _stdout, dispatched = self.replay([outcome, "accepted"])
                self.assertEqual(code, 1)
                self.assertEqual(list(report["case_outcomes"].values()), ["unverified"])
                self.assertEqual(report["failure_category"], "unverified_live_outcome")
                self.assertTrue(report["case_attribution"]["ticket-routing/high-priority"]["reason"])
                self.assertEqual(report["acceptance_counts"], {"accepted": 0, "rejected": 0})
                self.assertEqual([event["status"] for event in events], ["failed"])
                self.assertEqual(dispatched, ["high-priority"])
                self.assertIn("ticket-routing/normal-priority", report["not_run"])
                self.assertIn("stopped: unverified", stderr)
