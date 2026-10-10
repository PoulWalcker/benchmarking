"""Actual generation coordination with mocked Harbor and wrapper transport; no paid calls."""

import asyncio
import contextlib
import hashlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sapi_config_lab.coordinate.generate import main, summarize_trials
from sapi_config_lab.coordinate.native_tasks import image_tags, invoke, select_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.replay import load_selection, select_submission
from sapi_config_lab.coordinate.runs import Run, run_experiment
from sapi_config_lab.evaluate.records import NOT_EVALUATED
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.harbor_integration.yaml_agent import ReplayYamlAgent, WrapperYamlAgent
from sapi_config_lab.paths import workspace_root
from tests.test_selection import generation_run, save

ROOT = workspace_root()


def inspected_wrapper(root: Path) -> Path:
    """An inspected local wrapper with real temporary file bytes."""
    wrapper = root / "wrapper.py"
    wrapper.write_text("# inspected wrapper\n")
    identity = root / "inspection.json"
    host = HostConfig.from_environment()
    write_json(
        identity,
        {
            "schema": "sapi-lab-wrapper-identity/v1",
            "endpoint": host.wrapper_url,
            "dispatch": "codex-exec",
            "model": host.wrapper_model,
            "response_substitution": False,
            "wrapper_retries": 0,
            "provider_internal_retries": "unknown",
            "files": [{"name": "wrapper.py", "path": str(wrapper), "sha256": sha256(wrapper)}],
        },
    )
    return identity


class NativeGenerationTests(unittest.TestCase):
    def test_failed_task_invoke_preserves_stderr_in_the_run_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "tasks/invoice-total"
            task.mkdir(parents=True)
            (task / "task.toml").write_text("[metadata.sapi]\ndefault = true\n")
            (task / "experiment.py").write_text(
                "import sys\nsys.stderr.write('old\\r\\x1b[31mtask reason\\x1b[0m\\n')\nsys.exit(7)\n"
            )
            output = root / "run"
            seen = []

            def body(run):
                with self.assertRaises(subprocess.CalledProcessError) as raised:
                    with run.step("plan invoice-total"):
                        invoke(task, {"action": "plan"}, run.sources)
                self.assertEqual(raised.exception.returncode, 7)
                seen.append(raised.exception)
                raise raised.exception

            with (
                patch("sapi_config_lab.coordinate.native_tasks.resource_root", return_value=root),
                patch("sapi_config_lab.coordinate.runs.running_containers", return_value=""),
                patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
                patch(
                    "sapi_config_lab.coordinate.runs.docker_preflight",
                    return_value={"server_version": "27.0", "context": "test"},
                ),
                contextlib.redirect_stderr(io.StringIO()) as stderr,
            ):
                report = run_experiment(
                    output, {}, body, prefix="t", classify=lambda error: self.assertIs(error, seen[0]) or "task"
                )
            self.assertEqual(report["failure_stage"], "plan invoice-total")
            self.assertIsNone(report["log"])
            self.assertEqual(report["log_tail"], ["old", "task reason"])
            self.assertNotIn("  log:", stderr.getvalue())
            self.assertNotIn("  inspect:", stderr.getvalue())

    def test_generation_requires_inspection_before_creating_a_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            with (
                patch("sapi_config_lab.coordinate.runs.docker_preflight") as docker,
                patch("sapi_config_lab.coordinate.generate.run_logged") as controls,
                patch("sapi_config_lab.coordinate.runs.run_job") as harbor,
                patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper") as wrapper,
                contextlib.redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit) as raised,
            ):
                main(["--attempts", "1", "--report-dir", str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("--wrapper-evidence", stderr.getvalue())
            self.assertFalse(output.exists())
            controls.assert_not_called()
            harbor.assert_not_called()
            wrapper.assert_not_called()
            docker.assert_not_called()

    def test_invalid_inspection_refuses_before_controls_or_reservation(self):
        for fault in (
            "endpoint",
            "model",
            "missing-model",
            "files",
            "missing-files",
            "missing-inspection",
            "invalid-json",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                identity = inspected_wrapper(root)
                evidence = json.loads(identity.read_text())
                if fault in ("endpoint", "model"):
                    evidence[fault] = "different"
                elif fault == "missing-model":
                    evidence.pop("model")
                elif fault == "missing-files":
                    evidence["files"] = []
                elif fault == "files":
                    (root / "wrapper.py").write_text("changed")
                write_json(identity, evidence)
                if fault == "missing-inspection":
                    identity.unlink()
                elif fault == "invalid-json":
                    identity.write_text("invalid")
                output = root / "run"
                with (
                    patch("sapi_config_lab.coordinate.runs.running_containers", return_value=""),
                    patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
                    patch("sapi_config_lab.coordinate.runs.docker_preflight", return_value={"context": "test"}),
                    patch("sapi_config_lab.coordinate.generate.run_logged") as controls,
                    patch("sapi_config_lab.coordinate.runs.run_job") as harbor,
                    patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper") as wrapper,
                    contextlib.redirect_stderr(io.StringIO()),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(main(["--wrapper-evidence", str(identity), "--report-dir", str(output)]), 1)
                controls.assert_not_called()
                harbor.assert_not_called()
                wrapper.assert_not_called()
                self.assertFalse((output / "ledger.json").exists())
                self.assertEqual(json.loads((output / "report.json").read_text())["status"], "failed")

    def test_control_child_failure_names_its_stage_without_starting_generation(self):
        for child_stage in (None, "native build", "missing", "invalid", "list"):
            with self.subTest(child_stage=child_stage), tempfile.TemporaryDirectory() as directory:
                identity = inspected_wrapper(Path(directory))
                output = Path(directory) / "run"
                stderr, stdout = io.StringIO(), io.StringIO()

                def controls(argv, log, *, child_stage=child_stage, **kwargs):
                    child = log.parent / "control"
                    child.mkdir()
                    report = {"status": "failed"}
                    if child_stage == "native build":
                        report["failure_stage"] = child_stage
                    if child_stage == "invalid":
                        (child / "report.json").write_text("invalid json")
                    elif child_stage == "list":
                        write_json(child / "report.json", [])
                    elif child_stage != "missing":
                        write_json(child / "report.json", report)
                    log.write_text("child reason\n")
                    return 3

                with (
                    patch("sapi_config_lab.coordinate.runs.running_containers", return_value=""),
                    patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
                    patch(
                        "sapi_config_lab.coordinate.runs.docker_preflight",
                        return_value={"server_version": "27.0", "context": "test"},
                    ),
                    patch("sapi_config_lab.coordinate.generate.run_logged", side_effect=controls),
                    patch("sapi_config_lab.coordinate.runs.run_job") as harbor,
                    contextlib.redirect_stderr(stderr),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(
                        main(["--attempts", "1", "--wrapper-evidence", str(identity), "--report-dir", str(output)]), 1
                    )
                report = json.loads((output / "report.json").read_text())
                self.assertEqual(report["failure_stage"], "controls")
                self.assertEqual(report["log"], "control.log")
                self.assertEqual(report["log_tail"], ["child reason"])
                self.assertIn(
                    "control suite failed at native build" if child_stage == "native build" else "Control suite failed",
                    stderr.getvalue(),
                )
                harbor.assert_not_called()
                self.assertFalse((output / "ledger.json").exists())
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")

    def test_task_owned_prompts_preserve_every_original_arm_byte(self):
        expected = {
            ("invoice-total", "full"): "d1e72a8298a682f09c6198beb8e26f54beaf086f56a65a79a7cc68e0a0625f49",
            ("invoice-total", "scenario"): "8c43ed0f1fd947ba5430cd95cb5555a0575b183fa0d6fbc2883ca2c98981150a",
            ("research-report", "full"): "12e7a18a0881c4afeee2651ff00cfdcfae531e0e961a5df1c0b8a1e88399aff3",
            ("checkout-recovery", "full"): "920472a634ec32c6d55d66aceec6c4a66035d6cd69b5bc3ad36f3b3c315fae17",
        }
        sources = source_manifest()
        before = sorted((ROOT / "tasks").rglob("*.pyc"))
        for (name, arm), digest in expected.items():
            result = invoke(ROOT / "tasks" / name, {"action": "prompt", "catalog": arm}, sources)
            self.assertEqual(hashlib.sha256(result["prompt"].encode()).hexdigest(), digest)
        self.assertEqual(sorted((ROOT / "tasks").rglob("*.pyc")), before)
        with self.assertRaisesRegex(ValueError, "source mismatch"):
            invoke(ROOT / "tasks/invoice-total", {"action": "prompt", "catalog": "full"}, {})

    def test_generation_cli_reserves_before_exact_wrapper_call_and_uses_native_task(self):
        unknown_faults = {
            "timeout": "timeout",
            "reset": "transport_error",
            "incomplete-read": "transport_error",
            "invalid-json": "incomplete_receipt",
            "oversize": "incomplete_receipt",
            "non-object": "incomplete_receipt",
            "ok-false": "wrapper_unsettled",
            "nonzero-exit": "wrapper_unsettled",
            "bool-exit": "wrapper_unsettled",
        }
        evidence_faults = {
            "marker": "dispatch_unsettled",
            "missing-started": "evidence_unavailable",
            "missing-result": "evidence_unavailable",
            "corrupt": "evidence_unavailable",
            "corrupt-fields": "evidence_unavailable",
            "multiple-trials": "evidence_unavailable",
            "missing-nonstart": None,
            "prompt-hash": None,
            "empty-answer": None,
            "tool-marker": None,
        }
        for fault in (
            *unknown_faults,
            *evidence_faults,
            None,
            "control",
            "native-image",
            "source",
            "prompt",
            "phase",
            "wrapper-changed",
            "wrapper-before-first",
            "inspection-changed",
            "reported-model",
            "missing-model",
            "invalid-model",
        ):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                identity = inspected_wrapper(root)
                evidence = json.loads(identity.read_text())
                evidence["files"][0]["path"] = "/previous-machine/wrapper.py"
                write_json(identity, evidence)
                output = root / "run"
                tasks = select_tasks(ROOT / "tasks")
                sources = source_manifest()
                images = {tag: "sha256:" + tag for tag in image_tags(tasks)}
                (root / "reports").mkdir()
                write_json(root / "reports/native-image-build.json", {"sources": sources, "images": images})
                answer = "```yaml\r\n# exact authored answer\r\n```\r\n"
                calls = []
                ledger_path = root / "series/ledger.json" if fault == "reset" else output / "ledger.json"

                def controls(
                    argv, log, *, output=output, fault=fault, sources=sources, images=images, root=root, **kwargs
                ):
                    self.assertEqual(kwargs["stage"], "controls")
                    self.assertIsNone(kwargs["timeout"])
                    output = log.parent
                    control = output / "control"
                    control.mkdir()
                    write_json(control / "source-manifest.json", {} if fault == "source" else sources)
                    write_json(
                        control / "report.json",
                        {
                            "schema": "sapi-lab-harbor/v1",
                            "status": "failed" if fault == "control" else "passed",
                            "mode": "stub",
                            "source_unchanged": True,
                            "source_manifest": "source-manifest.json",
                            "image_id": "sha256:transport",
                            "native_images": {} if fault == "native-image" else images,
                            "transport": {"passed": True},
                            "oracle": {"passed": True},
                            "nop": {"passed": True},
                            "checks": [{"passed": True}],
                        },
                    )
                    if fault == "wrapper-before-first":
                        (root / "wrapper.py").write_text("changed during controls")
                    return 0

                def wrapper(
                    upstream,
                    prompt,
                    timeout,
                    maximum,
                    *,
                    output=output,
                    calls=calls,
                    answer=answer,
                    fault=fault,
                    ledger_path=ledger_path,
                ):
                    self.assertEqual((timeout, maximum), (195, 2_000_000))
                    event = json.loads(ledger_path.read_text())["events"][-1]
                    self.assertEqual((event["phase"], event["status"], event["count"]), ("authoring", "unknown", 1))
                    self.assertEqual(prompt.encode(), (output / "inputs/invoice-total/prompt.txt").read_bytes())
                    calls.append(prompt)
                    marker = json.loads(next(output.glob("jobs/*/*/agent/generation.json")).read_text())
                    self.assertEqual(marker["model_outcome"], "unknown")
                    self.assertEqual(marker["outcome_reason"], "dispatch_unsettled")
                    self.assertNotIn("duration_seconds", marker)
                    if fault == "timeout":
                        raise TimeoutError()
                    if fault == "reset":
                        raise ConnectionResetError()
                    if fault == "incomplete-read":
                        raise IncompleteRead(b"partial")
                    if fault in {"invalid-json", "oversize", "non-object"}:
                        return {"invalid-json": b"invalid", "oversize": b"x" * 2_000_001, "non-object": b"[]"}[fault]
                    if fault in {"ok-false", "nonzero-exit", "bool-exit"}:
                        return json.dumps(
                            {"ok": fault != "ok-false", "exit_code": False if fault == "bool-exit" else 1}
                        ).encode()
                    if fault == "empty-answer":
                        answer = ""
                    if fault == "tool-marker":
                        return json.dumps(
                            {
                                "ok": True,
                                "exit_code": 0,
                                "output": answer,
                                "stderr": "model: " + HostConfig.from_environment().wrapper_model + "\nexec\nsecret",
                            }
                        ).encode()
                    reported = {
                        "reported-model": "model: private-other-model",
                        "missing-model": "",
                        "invalid-model": "model: invalid/model",
                    }.get(fault, "model: " + HostConfig.from_environment().wrapper_model)
                    return json.dumps(
                        {"ok": True, "exit_code": 0, "output": answer, "stderr": reported + "\nPRIVATE"}
                    ).encode()

                def harbor(
                    argv,
                    task,
                    jobs,
                    name,
                    agent,
                    log,
                    *,
                    tasks=tasks,
                    answer=answer,
                    fault=fault,
                    root=root,
                    identity=identity,
                    **kwargs,
                ):
                    self.assertEqual(task, tasks[0])
                    self.assertEqual(kwargs["attempts"], "1")
                    self.assertIn("expected_model=" + HostConfig.from_environment().wrapper_model, kwargs["agent_keys"])
                    trial = jobs / name / "trial"
                    options = dict(value.split("=", 1) for value in kwargs["agent_keys"])
                    instance = WrapperYamlAgent(logs_dir=trial / "agent", **options)
                    environment = SimpleNamespace(upload_file=AsyncMock())
                    if fault in {
                        "marker",
                        "missing-started",
                        "missing-result",
                        "corrupt",
                        "corrupt-fields",
                        "multiple-trials",
                        "missing-nonstart",
                    }:
                        trial.mkdir(parents=True)
                        if fault != "missing-result":
                            save(
                                trial / "result.json",
                                {
                                    "task_name": task.name,
                                    "finished_at": "2026-10-10T00:00:00Z",
                                    "agent_execution": None
                                    if fault == "missing-nonstart"
                                    else {"started_at": "2026-10-10T00:00:00Z"},
                                },
                            )
                        if fault == "marker":
                            save(
                                trial / "agent/generation.json",
                                {"model_outcome": "unknown", "outcome_reason": "dispatch_unsettled"},
                            )
                        elif fault == "corrupt-fields":
                            save(trial / "agent/generation.json", {"model_outcome": [], "duration_seconds": 1})
                        elif fault == "corrupt":
                            (trial / "agent").mkdir()
                            (trial / "agent/generation.json").write_text('{"model_outcome":')
                        elif fault == "multiple-trials":
                            (jobs / name / "another").mkdir()
                        raise RuntimeError("Harbor process lost")
                    if fault == "prompt-hash":
                        instance.prompt_sha256 = "0" * 64
                    if (
                        fault
                        in (
                            "reported-model",
                            "missing-model",
                            "invalid-model",
                            "prompt-hash",
                            "empty-answer",
                            "tool-marker",
                        )
                        or fault in unknown_faults
                    ):
                        with self.assertRaisesRegex(RuntimeError, "YAML generation failed"):
                            asyncio.run(instance.run("unused native instruction", environment, SimpleNamespace()))
                        environment.upload_file.assert_not_awaited()
                        self.assertFalse((trial / "agent/submission.yaml").exists())
                        save(
                            trial / "result.json",
                            {
                                "task_name": task.name,
                                "exception_info": {"exception_type": "RuntimeError"},
                                "agent_execution": {"started_at": "2026-10-09T00:00:00Z"},
                            },
                        )
                        save(trial / "verifier/evaluation/report.json", {"passed": False})
                        return 0
                    asyncio.run(instance.run("unused native instruction", environment, SimpleNamespace()))
                    if fault == "inspection-changed":
                        changed = json.loads(identity.read_text())
                        changed["model"] = "changed-model"
                        write_json(identity, changed)
                    if fault == "wrapper-changed":
                        (root / "wrapper.py").write_text("changed after the first dispatch")
                    self.assertEqual((trial / "agent/submission.yaml").read_bytes(), answer.encode())
                    environment.upload_file.assert_awaited_once()
                    save(
                        trial / "result.json",
                        {
                            "task_name": task.name,
                            "verifier_result": {"rewards": {"reward": 1.0}},
                            "agent_execution": {"started_at": "2026-10-09T00:00:00Z"},
                        },
                    )
                    save(
                        trial / "verifier/evaluation/report.json",
                        {
                            "scenario": task.name,
                            "mode": "stub",
                            "passed": True,
                            "submission_sha256": sha256(trial / "agent/submission.yaml"),
                        },
                    )
                    save(trial / "verifier/result.json", {**NOT_EVALUATED, "execution": True, "acceptance": True})
                    return 0

                def task_invoke(task, request, sources, *, fault=fault):
                    if fault == "phase":
                        raise ValueError("Planned observations exceed the native verifier phase")
                    return invoke(task, request, sources)

                original_check = Run.check

                def check(run, phase, *, fault=fault, output=output, original_check=original_check):
                    if fault == "prompt" and phase.startswith("before-reservation"):
                        (output / "inputs/invoice-total/prompt.txt").write_text("changed")
                    original_check(run, phase)

                with (
                    patch("sapi_config_lab.coordinate.runs.workspace_root", return_value=root),
                    patch("sapi_config_lab.coordinate.runs.running_containers", return_value=""),
                    patch("sapi_config_lab.coordinate.runs.checked_harbor", return_value=(["harbor"], "0.21.0")),
                    patch(
                        "sapi_config_lab.coordinate.runs.docker_preflight",
                        return_value={"server_version": "27.0", "context": "test"},
                    ),
                    patch("sapi_config_lab.coordinate.runs.image_id", side_effect=images.__getitem__),
                    patch.object(Run, "use_image", return_value="sha256:transport"),
                    patch.object(Run, "check", check),
                    patch("sapi_config_lab.coordinate.generate.run_logged", side_effect=controls),
                    patch("sapi_config_lab.coordinate.generate.invoke", side_effect=task_invoke),
                    patch("sapi_config_lab.coordinate.runs.run_job", side_effect=harbor),
                    patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper", side_effect=wrapper),
                    contextlib.redirect_stdout(io.StringIO()) as stdout,
                    contextlib.redirect_stderr(io.StringIO()) as stderr,
                ):
                    code = main(
                        [
                            "--attempts",
                            "2"
                            if fault in ("wrapper-changed", "inspection-changed")
                            or fault in unknown_faults
                            or evidence_faults.get(fault)
                            else "1",
                            "--wrapper-evidence",
                            str(identity),
                            "--wrapper-file",
                            "wrapper.py=" + str(root / "wrapper.py"),
                            "--report-dir",
                            str(output),
                            *(
                                ["--series-dir", str(root / "series"), "--series-ceiling", "authoring=4"]
                                if fault == "reset"
                                else []
                            ),
                        ]
                    )
                    if fault == "reset":
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            retry = main(
                                [
                                    "--attempts",
                                    "2",
                                    "--wrapper-evidence",
                                    str(identity),
                                    "--wrapper-file",
                                    "wrapper.py=" + str(root / "wrapper.py"),
                                    "--report-dir",
                                    str(root / "retry"),
                                    "--series-dir",
                                    str(root / "series"),
                                ]
                            )
                        self.assertEqual(retry, 1)
                        self.assertEqual(len(calls), 1)
                        self.assertIn(
                            "earlier reservation has an unknown outcome",
                            json.loads((root / "retry/report.json").read_text())["error"],
                        )
                        self.assertFalse((root / "retry/jobs").exists())
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                progress = [
                    line.split("] ", 1)[1].split(" · ")[:2]
                    for line in stderr.getvalue().splitlines()
                    if line.startswith("[sapi-lab generate] ") and " · " in line and "/" in line.split()[2]
                ]
                prompt = output / "inputs/invoice-total/prompt.txt"
                for private in (
                    "PRIVATE",
                    "exact authored answer",
                    "private-other-model",
                    "invalid/model",
                    HostConfig.from_environment().wrapper_model,
                ):
                    self.assertNotIn(private, stderr.getvalue())
                if prompt.is_file() and prompt.read_text() != "changed":
                    self.assertNotIn(prompt.read_text()[:200], stderr.getvalue())
                if fault == "phase":
                    self.assertIn(("4/6 prompts", "failed"), [(a, b.split()[0]) for a, b in progress])
                    self.assertNotIn(["5/6 authoring 1/1", "running"], progress)
                self.assertEqual(code, int(fault is not None))
                self.assertEqual(
                    len(calls),
                    int(
                        fault in unknown_faults
                        or fault in {"empty-answer", "tool-marker"}
                        or fault
                        in (
                            None,
                            "wrapper-changed",
                            "inspection-changed",
                            "reported-model",
                            "missing-model",
                            "invalid-model",
                        )
                    ),
                )
                if fault in evidence_faults:
                    events = json.loads(ledger_path.read_text())["events"]
                    expected = "unknown" if evidence_faults[fault] else "failed"
                    self.assertEqual([(e["phase"], e["status"]) for e in events], [("authoring", expected)])
                    if expected == "unknown":
                        report = json.loads((output / "report.json").read_text())
                        self.assertEqual(report["unknown_outcome"]["reason"], evidence_faults[fault])
                        self.assertEqual(report["failure_category"], "unknown_outcome")
                        self.assertIn("OUTCOME UNKNOWN", stderr.getvalue())
                    else:
                        generation = next(output.glob("jobs/*/*/agent/generation.json"), None)
                        if generation:
                            self.assertEqual(
                                json.loads(generation.read_text())["model_outcome"],
                                "not_dispatched" if fault == "prompt-hash" else "settled",
                            )
                if fault in unknown_faults:
                    events = json.loads(ledger_path.read_text())["events"]
                    self.assertEqual([(e["phase"], e["status"]) for e in events], [("authoring", "unknown")])
                    report = json.loads((output / "report.json").read_text())
                    self.assertEqual(report["unknown_outcome"]["reason"], unknown_faults[fault])
                    self.assertEqual(
                        report["failure_category"],
                        "timeout_unknown_outcome" if fault == "timeout" else "unknown_outcome",
                    )
                    self.assertIn("OUTCOME UNKNOWN", stderr.getvalue())
                    self.assertIn("(not a failure verdict)", stderr.getvalue())
                    self.assertFalse((output / "jobs/generated-2-invoice-total").exists())
                if fault in ("wrapper-changed", "inspection-changed"):
                    events = json.loads(ledger_path.read_text())["events"]
                    self.assertEqual([(row["phase"], row["status"]) for row in events], [("authoring", "passed")])
                    self.assertFalse((output / "jobs/generated-2-invoice-total").exists())
                if fault == "wrapper-before-first":
                    self.assertEqual(json.loads(ledger_path.read_text())["events"], [])
                if fault in ("reported-model", "missing-model", "invalid-model"):
                    events = json.loads(ledger_path.read_text())["events"]
                    self.assertEqual([(row["phase"], row["status"]) for row in events], [("authoring", "failed")])
                    audit = json.loads(next(output.glob("jobs/*/*/agent/generation.json")).read_text())
                    self.assertEqual(audit["expected_model"], HostConfig.from_environment().wrapper_model)
                    self.assertEqual(audit["status"], "generation_error")
                    self.assertEqual(
                        audit["failure_reason"],
                        "model_identity_mismatch" if fault == "reported-model" else "model_identity_unverified",
                    )
                    with self.assertRaisesRegex(ValueError, "no later attempt is selected"):
                        select_submission(output / "report.json", ("invoice-total",))
                self.assertFalse((output / "task-packages").exists())
                if fault == "phase":
                    self.assertFalse((output / "ledger.json").exists())
                    self.assertFalse((output / "jobs").exists())
                if fault is None:
                    self.assertEqual(
                        [stage for stage, state in progress if state == "running"],
                        [
                            "1/6 setup",
                            "2/6 controls",
                            "3/6 native images",
                            "4/6 prompts",
                            "5/6 authoring 1/1",
                            "6/6 finalizing",
                        ],
                    )
                    self.assertIn("[sapi-lab generate]   1 authoring call reserved", stderr.getvalue())
                    self.assertIn(
                        "[sapi-lab generate]   Harbor job: generated-1-invoice-total · task invoice-total",
                        stderr.getvalue(),
                    )
                    self.assertIn("[1/1] invoice-total: passed", stderr.getvalue())
                    self.assertRegex(stderr.getvalue(), r"PASSED · (?:<1s|\d+s|\d+m\d\ds) · 6/6 stages done\n$")
                    report = json.loads((output / "report.json").read_text())
                    self.assertEqual(report["authoring_attempts_spent"], 1)
                    self.assertTrue(report["source_unchanged"])
                    self.assertIn("native_tasks", report)
                    self.assertEqual(report["wrapper_identity"], evidence)
                    self.assertEqual(report["wrapper_files_relocated"], ["wrapper.py"])
                    self.assertEqual((output / "wrapper-identity.json").read_bytes(), identity.read_bytes())
                    manifest = select_submission(output / "report.json", ("invoice-total",))
                    write_json(output / "selection.json", manifest)
                    selected = load_selection(output / "selection.json", copy_to=output / "replay-inputs")
                    self.assertEqual(selected["invoice-total"]["path"].read_bytes(), answer.encode())
                    audit = json.loads(next(output.glob("jobs/*/*/agent/generation.json")).read_text())
                    self.assertEqual((audit["generation_calls"], audit["repairs"]), (1, 0))
                    self.assertEqual(audit["model"], HostConfig.from_environment().wrapper_model)
                    self.assertEqual(audit["model"], audit["expected_model"])
                    self.assertNotIn("PRIVATE", json.dumps(audit))

    def test_cancelled_authoring_wait_keeps_a_durable_unknown_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            agent = WrapperYamlAgent(logs_dir=logs, upstream="mock://unused", expected_model="authorized")
            environment = SimpleNamespace(upload_file=AsyncMock())
            with (
                patch(
                    "sapi_config_lab.harbor_integration.yaml_agent.asyncio.to_thread",
                    side_effect=asyncio.CancelledError(),
                ),
                self.assertRaises(asyncio.CancelledError),
            ):
                asyncio.run(agent.run("prompt", environment, SimpleNamespace()))
            record = json.loads((logs / "generation.json").read_text())
            self.assertEqual((record["model_outcome"], record["outcome_reason"]), ("unknown", "cancelled"))
            environment.upload_file.assert_not_awaited()

    def test_a_failed_durable_marker_prevents_authoring_dispatch(self):
        from sapi_config_lab.evidence import durable_json

        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            agent = WrapperYamlAgent(logs_dir=logs, upstream="mock://unused", expected_model="authorized")
            writes = []

            def persist(path, record):
                writes.append(path)
                if len(writes) == 1:
                    raise OSError("cannot persist marker")
                durable_json(path, record)

            with (
                patch("sapi_config_lab.harbor_integration.yaml_agent.durable_json", side_effect=persist),
                patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper") as wrapper,
                self.assertRaisesRegex(RuntimeError, "YAML generation failed"),
            ):
                asyncio.run(agent.run("prompt", SimpleNamespace(upload_file=AsyncMock()), SimpleNamespace()))
            wrapper.assert_not_called()
            self.assertEqual(json.loads((logs / "generation.json").read_text())["model_outcome"], "not_dispatched")

    def test_another_reported_model_is_never_uploaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            logs = Path(temporary)
            agent = WrapperYamlAgent(logs_dir=logs, upstream="mock://unused", expected_model="authorized")
            environment = SimpleNamespace(upload_file=AsyncMock())
            with (
                patch(
                    "sapi_config_lab.harbor_integration.yaml_agent.request_wrapper",
                    return_value=json.dumps(
                        {"ok": True, "exit_code": 0, "output": "answer", "stderr": "model: private-other-model"}
                    ).encode(),
                ),
                self.assertRaisesRegex(RuntimeError, "YAML generation failed") as raised,
            ):
                asyncio.run(agent.run("prompt", environment, SimpleNamespace()))
            environment.upload_file.assert_not_awaited()
            self.assertFalse((logs / "submission.yaml").exists())
            audit = json.loads((logs / "generation.json").read_text())
            self.assertEqual(audit["expected_model"], "authorized")
            self.assertEqual(audit["failure_reason"], "model_identity_mismatch")
            self.assertNotIn("private-other-model", str(raised.exception))

    def test_authoring_agent_requires_an_explicit_expected_model(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(TypeError):
            WrapperYamlAgent(logs_dir=Path(temporary), upstream="mock://unused")

    def test_invalid_wrapper_answers_are_never_uploaded_and_replay_never_generates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, wrapper in enumerate(
                (
                    {"ok": False},
                    {"ok": True, "exit_code": 0, "output": "", "stderr": "model: authorized"},
                    {"ok": True, "exit_code": 0, "output": "x" * 100001, "stderr": "model: authorized"},
                    {"ok": True, "exit_code": 0, "output": "x", "stderr": "model: authorized\nexec\ncat private"},
                )
            ):
                agent = WrapperYamlAgent(
                    logs_dir=root / str(index), upstream="mock://unused", expected_model="authorized"
                )
                environment = SimpleNamespace(upload_file=AsyncMock())
                with (
                    patch(
                        "sapi_config_lab.harbor_integration.yaml_agent.request_wrapper",
                        return_value=json.dumps(wrapper).encode(),
                    ),
                    self.assertRaises(RuntimeError),
                ):
                    asyncio.run(agent.run("prompt", environment, SimpleNamespace()))
                environment.upload_file.assert_not_awaited()
                audit = json.loads((root / str(index) / "generation.json").read_text())
                self.assertEqual(
                    audit["failure_reason"],
                    (
                        "wrapper_unsuccessful",
                        "empty_or_oversized_generation",
                        "empty_or_oversized_generation",
                        "observed_tool_use",
                    )[index],
                )
            for index, raw in enumerate((b"invalid-json", b"x" * 2_000_001)):
                agent = WrapperYamlAgent(
                    logs_dir=root / ("raw-" + str(index)), upstream="mock://unused", expected_model="authorized"
                )
                environment = SimpleNamespace(upload_file=AsyncMock())
                with (
                    patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper", return_value=raw),
                    self.assertRaises(RuntimeError),
                ):
                    asyncio.run(agent.run("prompt", environment, SimpleNamespace()))
                environment.upload_file.assert_not_awaited()
            selected = root / "selected.yaml"
            selected.write_bytes(b"# exact\r\nworkflow: {}\r\n")
            agent = ReplayYamlAgent(
                logs_dir=root / "replay", submission_path=str(selected), submission_sha256=sha256(selected)
            )
            environment = SimpleNamespace(upload_file=AsyncMock())
            with patch("sapi_config_lab.harbor_integration.yaml_agent.request_wrapper") as generate:
                asyncio.run(agent.run("ignored", environment, SimpleNamespace()))
                generate.assert_not_called()
            self.assertEqual((root / "replay/submission.yaml").read_bytes(), selected.read_bytes())
            self.assertEqual(json.loads((root / "replay/replay.json").read_text())["generation_calls"], 0)
            selected.write_bytes(b"changed")
            environment.upload_file.reset_mock()
            with self.assertRaisesRegex(ValueError, "differs"):
                asyncio.run(agent.run("ignored", environment, SimpleNamespace()))
            environment.upload_file.assert_not_awaited()

    def test_tied_attempts_are_rejected_without_selecting_a_later_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = generation_run(
                root,
                {
                    "invoice-total": [
                        ("one", "2026-10-09T00:00:00Z", True),
                        ("two", "2026-10-09T00:00:00Z", True),
                    ]
                },
            )
            with patch("sapi_config_lab.coordinate.replay.source_manifest", return_value={"source.py": "frozen"}):
                with self.assertRaisesRegex(ValueError, "Ambiguous"):
                    select_submission(report, ("invoice-total",))

    def test_present_normalized_verdict_is_authoritative_for_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = generation_run(root, {"invoice-total": [("one", "2026-10-09T00:00:00Z", True)]})
            verdict = root / "jobs/generated-1/one/verifier/result.json"
            with patch("sapi_config_lab.coordinate.replay.source_manifest", return_value={"source.py": "frozen"}):
                for value in ({**NOT_EVALUATED, "acceptance": False}, {"acceptance": True}, NOT_EVALUATED):
                    save(verdict, value)
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        select_submission(report, ("invoice-total",))
                original = json.loads(report.read_text())
                save(report, {**original, "native_tasks": {"invoice-total": str(ROOT / "tasks/invoice-total")}})
                verdict.unlink()
                with self.assertRaisesRegex(ValueError, "missing its normalized verdict"):
                    select_submission(report, ("invoice-total",))
                save(verdict, {**NOT_EVALUATED, "execution": True, "acceptance": True})
                self.assertEqual(len(select_submission(report, ("invoice-total",))["entries"]), 1)

    def test_admission_is_eligible_with_null_facts_and_immutable_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = generation_run(root, {"checkout-recovery": [("first", "2026-10-09T00:00:00Z", True)]})
            trial = root / "jobs/generated-1/first"
            path = trial / "verifier/evaluation/report.json"
            acceptance = json.loads(path.read_text()) | {"schema": "sapi-lab-admission/v1"}
            save(path, acceptance)
            save(trial / "verifier/result.json", NOT_EVALUATED)
            self.assertTrue(summarize_trials(root / "jobs/generated-1")[0]["admitted"])
            with patch("sapi_config_lab.coordinate.replay.source_manifest", return_value={"source.py": "frozen"}):
                selected = select_submission(report, ("checkout-recovery",))
                save(root / "selection.json", selected)
                for relative in (*selected["entries"][0]["provenance"], "agent/submission.yaml"):
                    file = trial / relative
                    before = file.read_bytes()
                    file.write_bytes(before + b"\n")
                    with self.subTest(relative=relative), self.assertRaises(ValueError):
                        load_selection(root / "selection.json")
                    file.write_bytes(before)
                save(trial / "verifier/result.json", {**NOT_EVALUATED, "acceptance": True})
                with self.assertRaisesRegex(ValueError, "Admission"):
                    select_submission(report, ("checkout-recovery",))
