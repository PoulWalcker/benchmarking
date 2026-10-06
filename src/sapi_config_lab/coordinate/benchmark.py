"""Shared frozen-workflow runner for pinned synthetic business tasks and original scoring."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
from typing import Any
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
import tomllib
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import yaml

from sapi_config_lab.coordinate.benchmark_tasks import TASKS, task_definition
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.author.agent import audit_stderr
from sapi_config_lab.coordinate.controls import load_trials
from sapi_config_lab.execute.host import (
    build_image,
    harbor_command,
    harbor_run_args,
    image_id,
    run_logged,
    running_containers,
    staging_dir,
)
from sapi_config_lab.coordinate.provenance import source_manifest, host_environment
from sapi_config_lab.evaluate.task_evaluation import (
    build_run_log,
    evaluate,
    freeze_contract,
    judge,
    write_evaluation,
)
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.execute.agency import DispatchAudit, execute
from sapi_config_lab.autowfbench_source import PINNED_REVISION
from sapi_config_lab.execute.autowfbench import start_environment
from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.profile import read, read_bindings

ROOT = workspace_root()
UPSTREAM = "http://127.0.0.1:8765/run"
IMAGE = "sapi-config-lab-checkout:2.41.5"
CATALOG = ROOT / "generation/checkout-bindings.yaml"

# The benchmark agent's container must not be able to read the scoring oracle, so its
# image deletes these modules. `rm -rf` exits 0 on a path that does not exist, so a
# stale entry here silently removes nothing and leaves the oracle in place; a test in
# tests/test_packaging.py asserts every path below still exists under src/.
ORACLE_MODULES = (
    "sapi_config_lab/coordinate/benchmark.py",
    "sapi_config_lab/evaluate/task_evaluation.py",
    "sapi_config_lab/evaluate/judge_calibration.py",
    "sapi_config_lab/execute/autowfbench.py",
    "sapi_config_lab/autowfbench_source.py",
    "sapi_config_lab/autowfbench-source.json",
)


def oracle_scrub() -> str:
    """The rm -rf operands that strip the scoring oracle out of the agent's image."""
    return " ".join(f"/app/lab/src/{relative}" for relative in ORACLE_MODULES)


def save(path, data):
    # Unlike the shared writer, every checkout artifact may name a directory
    # that this run is the first to need.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)


def now():
    return datetime.now(timezone.utc).isoformat()


def inspect_wrapper():
    """Inspect known wrapper and effective model without dispatch or secret output."""
    wrapper = Path.home() / "n8n/codex_bridge.py"
    config = Path.home() / ".codex/config.toml"
    text = wrapper.read_text()
    # Fail closed if the inspected implementation differs from this known wrapper.
    if (
        '"codex",\n                    "exec",\n                    "--skip-git-repo-check",\n                    prompt,'
        not in text
        or "timeout=180" not in text
        or '"output": result.stdout.strip()' not in text
    ):
        raise ValueError("Wrapper needs fresh manual dispatch inspection")
    settings = tomllib.loads(config.read_text())
    model = settings.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("Pin a model in existing wrapper configuration")
    try:
        with urlopen(UPSTREAM, timeout=3) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    if status not in (200, 404, 405, 501):
        raise ValueError("Wrapper did not answer unpaid readiness probe")
    return {
        "schema": "sapi-lab-checkout-wrapper/v1",
        "endpoint": UPSTREAM,
        "model": model,
        "reasoning_effort": settings.get("model_reasoning_effort"),
        "dispatch": "codex-exec",
        "response_substitution": False,
        "wrapper_retries": 0,
        "provider_internal_retries": "unknown",
        "readiness_http_status": status,
        "files": [{"path": str(p), "sha256": sha256(p)} for p in (wrapper, config)],
    }


def prompt_for(contract):
    definition = contract.package["definition"]
    task = task_definition(definition["id"])
    output = "Your workflow output must contain a final_answer string."
    if task.artifact_field:
        output += f" Also output {task.artifact_field} as a string; it is submitted verbatim as {task.artifact_name}."
    return (
        "TASK\n"
        + definition["task"]
        + "\n"
        + definition["completion"]
        + f"\nBuild a workflow with id {task.key}-workflow. Use only the catalog below. Choose your own graph. "
        f"Runtime is {definition['limits']['wall_clock_seconds']} seconds with at most {task.runtime_model_cap} LLM operations. "
        "Tool results are JSON strings. "
        + output
        + " This is a synthetic simulator. "
        + task.authoring_notes
        + "\n\n"
        + (ROOT / "generation/FORMAT.md").read_text()
        + "\nOPERATION CATALOG\n"
        + (ROOT / task.catalog).read_text()
    )


def oracle_config(contract):
    path = ROOT / task_definition(contract.package["definition"]["id"]).oracle
    spec = importlib.util.spec_from_file_location("checkout_oracle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.oracle_config(contract)


def terminal_submission(record, elapsed, run_id, *, task=None, limit=120):
    task = task or task_definition("checkout")
    """Only timely, structurally valid terminal output is admitted as completion."""
    if elapsed > limit:
        return "timeout", None
    if record.get("status") != "success":
        return "solution_failed", None
    output = record.get("output")
    if not (
        isinstance(output, dict)
        and isinstance(output.get("final_answer"), str)
        and (task.artifact_field is None or isinstance(output.get(task.artifact_field), str))
    ):
        return "protocol_error", None
    return "completed", {
        "protocol_version": "1.0",
        "run_id": run_id,
        "status": "completed",
        "final_answer": output["final_answer"],
        "artifacts": (
            [{"name": task.artifact_name, "media_type": "text/markdown", "content": output[task.artifact_field]}]
            if task.artifact_field
            else []
        ),
        "trace": [],
    }


class TrialServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, directory, source, contract, mode, wrapper, seed=0):
        super().__init__(("0.0.0.0", 0), TrialHandler)
        self.directory, self.source, self.contract, self.mode, self.wrapper = directory, source, contract, mode, wrapper
        self.task = task_definition(contract.package["definition"]["id"])
        self.seed = seed
        self.limit = contract.package["definition"]["limits"]["wall_clock_seconds"]
        self.token = secrets.token_urlsafe(32)
        self.session: Any = None
        self.manager: Any = None
        self.timer: Any = None
        self.deadline_at: float | None = None
        self.started: Any = None
        self.started_at: Any = None
        self.finished = False
        self.audit: Any = None
        self.catalog = read_bindings(ROOT / self.task.catalog)
        self.run_id = "checkout-" + uuid.uuid4().hex

    def close_owned(self):
        if self.timer:
            self.timer.cancel()
        if self.manager:
            self.manager.__exit__(None, None, None)
        self.shutdown()
        self.server_close()


class TrialHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        server = self.server
        assert isinstance(server, TrialServer)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size < 4_000_000:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(size))
            if self.path == "/v1/agency/execute":
                if server.session is None or not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + server.session.connection["access_token"]
                ):
                    raise ValueError("Unauthorized runtime operation")
                if server.audit is None:
                    raise ValueError("No admitted runtime model calls")
                result = execute(data, server.catalog, UPSTREAM, 120, audit=server.audit, reject_tool_use=True)
                result = {k: v for k, v in result.items() if k not in ("provider", "model")}
            else:
                if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + server.token):
                    raise ValueError("Unauthorized trusted verifier")
                if self.path == "/begin":
                    result = self.begin()
                elif self.path == "/finish":
                    result = self.finish_trial(data["record"])
                else:
                    raise ValueError("Unknown route")
            raw = json.dumps(result).encode()
            self.send_response(200)
        except Exception as error:
            save(server.directory / "server-error.json", {"type": type(error).__name__, "message": str(error)})
            raw = json.dumps({"error": type(error).__name__}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def begin(self):
        s = self.server
        assert isinstance(s, TrialServer)
        if s.started is not None:
            raise ValueError("Trial already started")
        s.manager = start_environment(
            s.source, s.task.challenge_id, s.seed, bind_host="0.0.0.0", public_host="host.docker.internal"
        )
        s.session = s.manager.__enter__()
        s.started = time.monotonic()
        s.deadline_at = time.time() + s.limit
        s.started_at = now()
        deadline = s.deadline_at
        s.timer = threading.Timer(s.limit, s.session.finalize)
        s.timer.start()
        if s.mode == "candidate":
            cfg = read(s.directory / "submission.yaml")
            counts: dict[str, int] = {}
            for step in cfg["workflow"]["steps"]:
                if step["kind"] == "LLM":
                    counts[step["uses"]] = counts.get(step["uses"], 0) + 1
            s.audit = DispatchAudit(
                s.directory / "runtime-dispatch.jsonl",
                {
                    "max_attempts": sum(counts.values()),
                    "operations": counts,
                    "model": s.wrapper["model"],
                    "occurrences": {
                        f"{cfg['workflow']['id']}/r{cfg['workflow']['revision']}/{x['id']}": x["uses"]
                        for x in cfg["workflow"]["steps"]
                        if x["kind"] == "LLM"
                    },
                    "expires_at": deadline,
                },
            )
        return {
            "llm_mode": "live" if s.mode == "candidate" else "stub",
            "bridge_url": f"http://host.docker.internal:{s.server_port}",
            "operation_url": s.session.connection["base_url"] + "/tools",
            "operation_token": s.session.connection["access_token"],
            "deadline_at": deadline,
        }

    def finish_trial(self, record):
        s = self.server
        assert isinstance(s, TrialServer)
        if s.finished:
            raise ValueError("Trial already finalized")
        s.finished = True
        ended = time.monotonic()
        elapsed = ended - s.started
        s.timer.cancel()
        evidence = s.session.finalize()
        save(s.directory / "environment-evidence.json", evidence)
        if hasattr(s.session, "transport_evidence"):
            save(s.directory / "transport-evidence.json", s.session.transport_evidence())
        save(s.directory / "native-record.json", record)
        reason, submission = terminal_submission(record, elapsed, s.run_id, task=s.task, limit=s.limit)
        run = build_run_log(
            s.contract,
            evidence,
            submission,
            run_id=s.run_id,
            seed=s.seed,
            started_at=s.started_at,
            finished_at=now(),
            duration_seconds=elapsed,
            termination_reason=reason,
            solution={"id": s.mode, "name": "Frozen YAML in real n8n", "version": "1", "runtime": "n8n-2.41.5"},
        )
        save(s.directory / "run-log.json", run)
        reply = None
        error = None
        if s.contract.judge_mode == "codex":
            # Durable counted attempt immediately before untouched independent judge.
            save(
                s.directory / "judge-dispatch.json",
                {"attempts": 1, "model": s.contract.judge_model, "started_at": now()},
            )
        try:
            reply = judge(s.contract, run, s.directory / "judge", timeout=180)
            save(s.directory / "judge-reply.json", reply)
        except Exception as exc:
            error = type(exc).__name__
        report = evaluate(s.contract, run, reply, judge_error=error)
        write_evaluation(s.directory / "evaluation", report)
        return report


def native_trial(directory, source, contract, mode, wrapper, config, *, seed=0):
    directory.mkdir(parents=True, exist_ok=False)
    if config is not None:
        (directory / "submission.yaml").write_text(config)
    definition = task_definition(contract.package["definition"]["id"])
    server = TrialServer(directory, source, contract, mode, wrapper, seed)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    staging = staging_dir("sapi-checkout-")
    try:
        task = staging / "tasks" / definition.challenge_id
        for name in ("environment", "tests", "solution"):
            (task / name).mkdir(parents=True)
        (task / "instruction.md").write_text(
            "Execute the frozen YAML benchmark submission. No authoring or repair is permitted.\n"
        )
        (task / "task.toml").write_text(
            (ROOT / "harbor/templates/task.toml").read_text().replace("invoice-total", definition.challenge_id)
        )
        (task / "environment/Dockerfile").write_text(
            f"FROM {IMAGE}\nUSER root\nRUN rm -rf /app/lab/benchmarks /app/scenario "
            f"{oracle_scrub()} && mkdir -p /app/submission\n"
        )
        if config is not None:
            (task / "solution/config.yaml").write_text(config)
            (task / "solution/solve.sh").write_text(
                "#!/bin/bash\nset -euo pipefail\ncp /solution/config.yaml /app/submission/config.yaml\n"
            )
        else:
            (task / "solution/solve.sh").write_text("#!/bin/bash\ntrue\n")
        shutil.copyfile(ROOT / definition.catalog, task / "tests/bindings.yaml")
        save(
            task / "tests/connection.json",
            {"url": f"http://host.docker.internal:{server.server_port}", "token": server.token},
        )
        (task / "tests/test.sh").write_text(
            "#!/bin/bash\nset -euo pipefail\npython3 -m sapi_config_lab.coordinate.benchmark_worker\n"
        )
        shutil.copytree(task, directory / "task-package")
        save(directory / "task-package/tests/connection.json", {"url": "runtime-local", "token": "REDACTED"})
        argv = harbor_run_args(
            harbor_command(), staging / "tasks", staging / "jobs", "trial", "oracle" if config else "nop"
        )
        rc = run_logged(argv, directory / "harbor.log", timeout=900)
        if (staging / "jobs").exists():
            shutil.copytree(staging / "jobs", directory / "jobs")
        trials = load_trials(directory / "jobs/trial")
        tokens = [server.token]
        if server.session is not None:
            tokens.append(server.session.connection["access_token"])
        leaked = [
            str(path.relative_to(directory))
            for path in directory.rglob("*")
            if path.is_file() and any(token.encode() in path.read_bytes() for token in tokens)
        ]
        result = {"harbor_exit_code": rc, "trials": trials, "ephemeral_token_leak_check": not leaked}
        if leaked:
            raise ValueError("Ephemeral token appeared in persisted trial artifacts")
        save(directory / "harbor-result.json", result)
        return result
    finally:
        server.close_owned()
        thread.join(timeout=2)
        shutil.rmtree(staging)


def author(prompt, directory, identity, task=None):
    task = task or task_definition("checkout")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "prompt.txt").write_text(prompt)
    save(directory / "dispatch.json", {"attempts": 1, "started_at": now(), "requested_model": identity["model"]})
    if any(sha256(row["path"]) != row["sha256"] for row in identity["files"]):
        raise ValueError("Wrapper identity changed before dispatch")
    started = time.monotonic()
    record: dict[str, Any] = {"eligible": False, "repairs": 0}
    try:
        request = Request(UPSTREAM, json.dumps({"prompt": prompt}).encode(), {"Content-Type": "application/json"})
        with urlopen(request, timeout=190) as response:
            wrapper = json.load(response)
        record.update(audit_stderr(wrapper.get("stderr", "")))
        answer = wrapper.get("output", "")
        (directory / "submission.yaml").write_text(answer)
        if not wrapper.get("ok") or wrapper.get("exit_code") != 0:
            raise ValueError("Wrapper failed")
        if record["model"] != identity["model"] or record["observed_tool_markers"]:
            raise ValueError("Author identity/tool gate")
        cfg = read(directory / "submission.yaml")
        if sum(x["kind"] == "LLM" for x in cfg["workflow"]["steps"]) > task.runtime_model_cap:
            raise ValueError("Runtime cap exceeded")
        if cfg["execution"]["deadline_seconds"] != 120:
            raise ValueError("Original deadline required")
        N8nBackend().compile(
            cfg,
            read_bindings(ROOT / task.catalog),
            CompileOptions(
                llm_mode="live",
                bridge_url="http://localhost:1",
                operation_url="http://localhost:2/tools",
            ),
        )
        record["eligible"] = True
    except Exception as error:
        record["error_type"] = type(error).__name__
        record["error"] = str(error)
    record["duration_seconds"] = time.monotonic() - started
    save(directory / "authoring.json", record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--task", choices=sorted(TASKS), default="checkout")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--mode", choices=("prepare", "controls", "live"), default="prepare")
    parser.add_argument("--source", type=Path, default=ROOT / ".cache/autowfbench" / PINNED_REVISION)
    parser.add_argument("--judge-model", default="gpt-6-astra")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--controls-report", type=Path, help="Reuse fresh source-matched unpaid controls")
    args = parser.parse_args()
    task = task_definition(args.task)
    output = args.report_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    identity = inspect_wrapper()
    save(output / "wrapper-identity.json", identity)
    contract = freeze_contract(
        args.source,
        task.challenge_id,
        judge_model=args.judge_model,
        judge_mode="demo" if args.mode == "controls" else "codex",
    )
    save(output / "task-contract.json", contract.as_dict())
    save(output / "task-definition.json", task.as_dict())
    save(output / "host-environment.json", host_environment())
    prompt = prompt_for(contract)
    (output / "authoring-prompt.txt").write_text(prompt)
    manifest = source_manifest()
    save(output / "source-manifest.json", manifest)
    plan = {
        "schema": "sapi-lab-checkout-plan/v1",
        "authoring_attempts": task.authoring_attempts,
        "selected": "chronological first eligible",
        "runtime_attempts_max": task.runtime_model_cap,
        "judge_attempts_max": 2 if task.live_reference else 1,
        "total_actual_model_attempts_max": task.authoring_attempts
        + task.runtime_model_cap
        + (2 if task.live_reference else 1),
        "authoring_repairs": 0,
        "runtime_retries": 0,
        "judge_retries": 0,
        "seed": args.seed,
        "task": task.key,
        "requested_model": identity["model"],
        "judge_model": args.judge_model,
        "runtime_seconds": 120,
        "runtime_timer": "after environment provision, before compile",
        "authoring": "separate prebuilt-workflow phase; included in full pipeline time",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "contract_digest": contract.as_dict()["contract_digest"],
        "control_judge": "explicit simulated demo" if args.mode == "controls" else "independent codex",
    }
    save(output / "plan.json", plan)
    if args.mode == "prepare":
        print(json.dumps({"plan": str(output / "plan.json"), "model_calls": 0}))
        return 0
    started = time.monotonic()
    before = running_containers()
    (output / "containers-before.txt").write_text(before)
    if not args.skip_build:
        build_image(IMAGE, output / "image-build.log")
    base_image_id = image_id(IMAGE)
    if args.mode == "live":
        controls = args.controls_report
        if controls is None:
            controls = output / "controls/report.json"
            rc = run_logged(
                [
                    sys.executable,
                    "-m",
                    "sapi_config_lab.coordinate.benchmark",
                    "--mode",
                    "controls",
                    "--task",
                    task.key,
                    "--seed",
                    str(args.seed),
                    "--report-dir",
                    str(controls.parent),
                    "--source",
                    str(args.source),
                    "--skip-build",
                ],
                output / "controls.log",
                timeout=900,
            )
            if rc:
                raise RuntimeError("Unpaid controls failed; no authoring dispatched")
        gate = json.loads(controls.read_text())
        if (
            gate.get("task") != task.key
            or gate.get("seed") != args.seed
            or gate.get("mode") != "controls"
            or gate.get("status") != "completed"
            or not gate.get("source_unchanged")
            or gate.get("actual_model_attempts") != 0
            or json.loads((controls.parent / "source-manifest.json").read_text()) != manifest
            or gate.get("image_id") != base_image_id
        ):
            raise ValueError("Live dispatch requires fresh source/image-matched unpaid controls")
        save(output / "controls-gate.json", {"report": str(controls.resolve()), "sha256": sha256(controls)})
    report = {
        "mode": args.mode,
        "task": task.key,
        "seed": args.seed,
        "image_id": base_image_id,
        "started_at": now(),
        "authoring": [],
        "status": "failed",
        "limitations": [
            "Authoring uses unchanged wrapper cwd outside repository; no-tools prompt and stderr audit are not enforced sandboxing.",
            "Candidate is schema-constrained YAML; trusted verifier files are in same container, not an adversarial arbitrary-code sandbox.",
            "Judge prompt rejects candidate instructions; prompt-injection robustness has not been established.",
        ],
    }
    try:
        if args.mode == "controls":
            for mode, cfg in [("reference", yaml.safe_dump(oracle_config(contract), sort_keys=False)), ("nop", None)]:
                report[mode] = native_trial(output / mode, args.source, contract, mode, identity, cfg, seed=args.seed)
        else:
            for index in range(task.authoring_attempts):
                report["authoring"].append(author(prompt, output / f"authoring-{index + 1}", identity, task))
            eligible = next((index + 1 for index, r in enumerate(report["authoring"]) if r["eligible"]), None)
            report["selected_authoring"] = eligible
            if task.live_reference:
                report["reference"] = native_trial(
                    output / "reference",
                    args.source,
                    contract,
                    "reference",
                    identity,
                    yaml.safe_dump(oracle_config(contract), sort_keys=False),
                )
            if eligible:
                raw = (output / f"authoring-{eligible}/submission.yaml").read_text()
                report["candidate"] = native_trial(
                    output / "candidate", args.source, contract, "candidate", identity, raw, seed=args.seed
                )
    finally:
        report["full_pipeline_seconds"] = time.monotonic() - started
        report["source_unchanged"] = source_manifest() == manifest
        report["finished_at"] = now()
        (output / "containers-after.txt").write_text(running_containers())
        evaluated_names = (
            ["reference", "nop"]
            if args.mode == "controls"
            else (["reference", "candidate"] if task.live_reference else ["candidate"])
        )
        paths = [output / name / "evaluation/evaluation.json" for name in evaluated_names]
        evaluations = [json.loads(path.read_text()) if path.exists() else None for path in paths]
        from sapi_config_lab.evaluate.task_evaluation import is_evaluation, summarize_evaluations

        for name, evaluation in zip(evaluated_names, evaluations):
            report[name + "_evaluation"] = summarize_evaluations([evaluation] if (output / name).exists() else [])
        report["planned_trials_not_started"] = [name for name in evaluated_names if not (output / name).exists()]
        reference = evaluations[0] if "reference" in evaluated_names else None
        candidate = evaluations[-1]
        if args.mode == "controls":
            completed = bool(
                is_evaluation(reference)
                and reference.get("execution_pass") is True
                and reference.get("normalized_reward") == 0.732
                and is_evaluation(candidate)
                and candidate.get("execution_pass") is False
                and candidate.get("normalized_reward") is None
                and report.get("reference", {}).get("ephemeral_token_leak_check") is True
                and report.get("nop", {}).get("ephemeral_token_leak_check") is True
                and len(report.get("reference", {}).get("trials", [])) == 1
                and report["reference"]["trials"][0].get("rewards") == {"reward": 0.732}
                and report["reference"]["trials"][0].get("exception") is None
            )
        else:
            completed = bool(
                report.get("selected_authoring")
                and all(is_evaluation(item) and item.get("status") == "complete" for item in evaluations)
                and all(
                    report.get(name, {}).get("harbor_exit_code") == 0
                    and report.get(name, {}).get("trials")
                    and all(not trial.get("exception") for trial in report[name]["trials"])
                    for name in evaluated_names
                )
            )
        report["status"] = "completed" if completed and report["source_unchanged"] else "failed"
        report["actual_model_attempts"] = (
            len(list(output.glob("authoring-*/dispatch.json")))
            + len(list(output.glob("*/judge-dispatch.json")))
            + sum(
                sum(json.loads(line).get("event") == "dispatch_attempt" for line in p.read_text().splitlines())
                for p in output.glob("*/runtime-dispatch.jsonl")
            )
        )
        from sapi_config_lab.evaluate.benchmark_series import stage_summary

        report["stage_summary"] = stage_summary(output)
        save(output / "report.json", report)
    print(json.dumps({"report": str(output / "report.json")}))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
