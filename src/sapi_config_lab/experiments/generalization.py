"""Two finite semantic-composition experiments on the unchanged static catalog."""

from __future__ import annotations

import argparse
import copy
import importlib
import json
from http.server import HTTPServer
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any

from sapi_config_lab.experiments.expansion import ExpansionSeries
from sapi_config_lab.experiments.generation.common import summarize_trials
from sapi_config_lab.experiments.harbor import load_trials
from sapi_config_lab.experiments.host import harbor_command
from sapi_config_lab.experiments.lifecycle_run import bind_authoring_evidence, file_hash, read_json, MODEL
from sapi_config_lab.experiments.live_evidence import load_verifier, reconcile_dispatches
from sapi_config_lab.experiments.provenance import source_manifest
from sapi_config_lab.experiments.replay import require
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.runtime.agency import make_handler
from sapi_config_lab.runtime.execution import run_case
from sapi_config_lab.runtime.lifecycle import durable_json
from sapi_config_lab.core import profile

TASKS = ("billing-bulletin-packet", "two-audience-briefs")
CAPS = {TASKS[0]: {"base": 1, "alternate": 1}, TASKS[1]: {"base": 6, "alternate": 6}}


class GeneralizationSeries(ExpansionSeries):
    schema = "sapi-lab-generalization-series/v1"
    runtime_caps = CAPS

    @staticmethod
    def select(scenarios=None):
        require(scenarios is None or scenarios == TASKS, "The finite composition series has two ordered tasks")
        return TASKS

    def reserve(self, scenario, phase, name, report):
        require(not (self.directory / "stopped.json").exists(), "Composition series has stopped")
        return super().reserve(scenario, phase, name, report)


def data_dir():
    return workspace_root() / "generation/generalization"


def stage(destination, scenario, image, *, controls=False):
    """New task namespace, existing Harbor distribution and agent interfaces."""
    require(scenario in TASKS, "Unknown composition task")
    root = workspace_root()
    destination.mkdir(parents=True, exist_ok=False)
    descriptions = read_json(data_dir() / "tasks.json")
    cases = read_json(data_dir() / "cases.json")
    references = sorted((data_dir() / "controls").glob(scenario + "-*.yaml")) if controls else [None]
    hashes = {}
    for reference in references:
        name = reference.stem if reference else scenario
        task = destination / name
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        instruction = (
            "TASK\n"
            + descriptions[scenario]
            + "\n\nFORMAT\n"
            + (root / "generation/FORMAT.md").read_text()
            + "\n\nPROFILE\n"
            + (root / "docs/PROFILE.md").read_text()
            + "\n\nOPERATION CATALOG\n"
            + CATALOG.read_text()
        )
        (task / "instruction.md").write_text(instruction)
        hashes[name] = file_hash(task / "instruction.md")
        dockerfile = f"FROM {image}\nUSER root\nWORKDIR /app\n"
        if reference:
            shutil.copyfile(reference, task / "environment/base.yaml")
            (task / "solution").mkdir()
            shutil.copyfile(root / "harbor/templates/solve.sh", task / "solution/solve.sh")
            dockerfile += "COPY base.yaml /app/scenario/base.yaml\nRUN mkdir -p /app/submission\n"
        else:
            dockerfile += "RUN rm -rf /app/lab/configs /app/scenario /app/submission && mkdir -p /app/submission\n"
        (task / "environment/Dockerfile").write_text(dockerfile)
        (task / "task.toml").write_text(
            (root / "harbor/templates/task.toml").read_text().replace('name = "invoice-total"', f'name = "{name}"')
        )
        script = (
            (root / "harbor/templates/test.sh")
            .read_text()
            .replace("@SCENARIO@", scenario)
            .replace("/tests/verify.py", "/tests/generalization_submission.py")
        )
        (task / "tests/test.sh").write_text(script)
        for path in (root / "verification").glob("*.py"):
            shutil.copyfile(path, task / "tests" / path.name)
        (task / "tests/cases.json").write_text(json.dumps({scenario: cases[scenario]}, indent=2) + "\n")
    return hashes


def fresh_cases(scenario):
    cases = copy.deepcopy(read_json(data_dir() / "cases.json")[scenario])
    marker = secrets.token_hex(8)
    offset = secrets.randbelow(500) + 1
    for case in cases:
        for field, value in case["inputs"].items():
            if isinstance(value, str):
                case["inputs"][field] = f"Fixture {marker}. " + value
            elif field == "articles":
                for article in value:
                    article["id"] = marker + "-" + article["id"]
                    article["text"] = f"Fixture {marker}. " + article["text"]
            elif field == "invoices":
                for invoice in value:
                    invoice["id"] = marker + "-" + invoice["id"]
                    if invoice["amount_minor"] > 0:
                        invoice["amount_minor"] += offset
    return {scenario: cases}


def harbor_args(tasks, jobs, name, agent):
    return [
        *harbor_command(),
        "run",
        "--path",
        str(tasks),
        "--agent",
        agent,
        "--n-attempts",
        "1",
        "--n-concurrent",
        "1",
        "--max-retries",
        "0",
        "--jobs-dir",
        str(jobs),
        "--job-name",
        name,
        "--force-build",
    ]


def command(args, log):
    with log.open("w") as stream:
        return subprocess.run(
            args, cwd=workspace_root(), stdout=stream, stderr=subprocess.STDOUT, timeout=1800
        ).returncode


def controls(directory, scenario, image, frozen):
    directory.mkdir()
    image_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
    tag = "sapi-config-lab-generalization-base:" + image_id.split(":")[-1][:16]
    subprocess.check_call(["docker", "tag", image_id, tag])
    stage(directory / "tasks", scenario, tag, controls=True)
    expected_sources = {path.parent.parent.name: path for path in (directory / "tasks").glob("*/environment/base.yaml")}
    report: dict[str, Any] = {"status": "failed", "image": tag, "image_id": image_id, "checks": []}
    try:
        for agent in ("oracle", "nop"):
            args = harbor_args(directory / "tasks", directory / "jobs", agent, agent)
            rc = command(args, directory / (agent + ".log"))
            trials = load_trials(directory / "jobs" / agent)
            passed = (
                rc == 0
                and len(trials) == 2
                and all(
                    not row["exception"] and row["rewards"] == {"reward": 1.0 if agent == "oracle" else 0.0}
                    for row in trials
                )
                and {row["task_name"] for row in trials} == set(expected_sources)
            )
            if agent == "oracle" and passed:
                passed = all(
                    row["acceptance"].get("passed") is True
                    and row["acceptance"].get("submission_sha256") == file_hash(expected_sources[row["task_name"]])
                    and (Path(row["result_path"]).parent / "verifier/submission.yaml").read_bytes()
                    == expected_sources[row["task_name"]].read_bytes()
                    and row["acceptance"].get("runtime_source_manifest")
                    == {p: h for p, h in frozen.items() if p.startswith("src/")}
                    for row in trials
                )
            report["checks"].append({"agent": agent, "passed": passed, "trials": trials, "command": args})
            require(passed, "Two-graph native " + agent + " control failed")
        require(source_manifest() == frozen, "Sources changed during controls")
        report["status"] = "passed"
    finally:
        durable_json(directory / "report.json", report)
    return report


def finish_report(series, directory, report):
    report["source_unchanged"] = source_manifest() == series.data["source_manifest"]
    report["budget"] = {"series": series.ceilings, "runtime_case_caps": CAPS[report["scenario"]]}
    if not report["source_unchanged"]:
        report.update(status="failed", error="Sources changed during experiment")
    durable_json(directory / "report.json", report)
    if report["status"] != "passed":
        durable_json(
            series.directory / "stopped.json",
            {"scenario": report["scenario"], "report": str(directory / "report.json"), "reason": report.get("error")},
        )


def author(series, scenario, *, image, upstream):
    directory = series.directory / scenario / "authoring"
    directory.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "scenario": scenario,
        "status": "failed",
        "trials": [],
        "origin": "generated",
        "authoring_attempts": 2,
    }
    reservation = None
    try:
        require(not (series.directory / "stopped.json").exists(), "Composition series has stopped")
        report["controls"] = controls(directory / "controls", scenario, image, series.data["source_manifest"])
        prompts = stage(directory / "tasks", scenario, report["controls"]["image"])
        report["prompt_sha256"] = prompts[scenario]
        private = directory / "tasks" / scenario / "tests/cases.json"
        durable_json(private, fresh_cases(scenario))
        report["private_cases_sha256"] = file_hash(private)
        for attempt in (1, 2):
            reservation = series.reserve(scenario, "authoring", str(attempt), directory / "report.json")
            args = harbor_args(
                directory / "tasks",
                directory / "jobs",
                f"attempt-{attempt}",
                "sapi_config_lab.experiments.generation.agent:WrapperYamlAgent",
            )
            args += ["--ak", "upstream=" + upstream]
            rc = command(args, directory / f"author-{attempt}.log")
            trials = summarize_trials(directory / "jobs" / f"attempt-{attempt}")
            report["trials"].extend(trials)
            require(rc == 0 and len(trials) == 1 and trials[0]["passed"], "One-shot authoring/native acceptance failed")
            raw, generation = bind_authoring_evidence(
                trials[0], report["prompt_sha256"], report["private_cases_sha256"], series.data["source_manifest"]
            )
            require(
                generation.get("model") == MODEL
                and generation.get("generation_calls") == 1
                and generation.get("repairs") == 0
                and not generation.get("observed_tool_markers"),
                "Unexpected model authoring provenance",
            )
            if attempt == 1:
                selected = series.directory / scenario / "submission.yaml"
                selected.write_bytes(raw)
                report["submission_sha256"] = file_hash(selected)
            series.finish(reservation, True)
            reservation = None
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if reservation is not None:
            series.finish(reservation, False)
    finally:
        finish_report(series, directory, report)
    return report


def live(series, scenario, *, upstream):
    directory = series.directory / scenario / "live"
    directory.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "scenario": scenario,
        "status": "failed",
        "cases": [],
        "origin": "selected_first_generated_submission",
        "human_review": "not_claimed",
        "qualitative_review": "pending_independent_agent_review",
    }
    reservation = None
    try:
        require(not (series.directory / "stopped.json").exists(), "Composition series has stopped")
        authored = read_json(series.directory / scenario / "authoring/report.json")
        require(authored["status"] == "passed" and authored["source_unchanged"] is True, "Authoring gate failed")
        source = series.directory / scenario / "submission.yaml"
        require(file_hash(source) == authored["submission_sha256"], "Selected original YAML changed")
        private = series.directory / scenario / "authoring/tasks" / scenario / "tests/cases.json"
        require(file_hash(private) == authored["private_cases_sha256"], "Private cases changed")
        original = profile.read(source)
        verification, _ = load_verifier()
        checker = importlib.import_module(verification.__package__ + ".generalization")
        cases = read_json(private)[scenario]
        require([case["name"] for case in cases] == list(CAPS[scenario]), "Frozen case order changed")
        report["submission_sha256"] = file_hash(source)
        report["private_cases_sha256"] = file_hash(private)
        report["config_transformations"] = [
            "workflow.inputs replaced by frozen private fixture",
            "execution.deadline_seconds set to 600",
        ]
        for case in cases:
            config = copy.deepcopy(original)
            config["workflow"]["inputs"] = case["inputs"]
            config["execution"]["deadline_seconds"] = 600
            plan = checker.analyze(config, case)
            operations: dict[str, int] = {}
            for operation in plan["model_occurrences"].values():
                operations[operation] = operations.get(operation, 0) + 1
            require(
                0 < len(plan["model_occurrences"]) <= CAPS[scenario][case["name"]], "Runtime exceeds declared case cap"
            )
            reservation = series.reserve(scenario, "runtime", case["name"], directory / "report.json")
            expires = time.time() + 600
            prefix = f"{config['workflow']['id']}/r{config['workflow']['revision']}/"
            budget: dict[str, Any] = {
                "max_attempts": len(plan["model_occurrences"]),
                "operations": operations,
                "model": MODEL,
                "occurrences": {prefix + sid: op for sid, op in plan["model_occurrences"].items()},
                "expires_at": expires,
            }
            path = directory / case["name"]
            path.mkdir()
            durable_json(path / "budget.json", budget)
            audit = path / "agency.jsonl"
            server = HTTPServer(
                ("127.0.0.1", 0), make_handler(profile.read_bindings(CATALOG), upstream, 185, audit, budget)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                record = run_case(
                    config,
                    path / "native",
                    llm_mode="live",
                    bridge_url=f"http://127.0.0.1:{server.server_address[1]}",
                    deadline_at=expires,
                )
                checked = checker.verify_execution(config, case, record, mode="live")
                audit_rows = [json.loads(line) for line in audit.read_text().splitlines()]
                correlation = reconcile_dispatches(checked["calls"], audit_rows, MODEL)
                require(0 < len(correlation) <= budget["max_attempts"], "Missing or excess native model call")
                report["cases"].append(
                    {
                        "name": case["name"],
                        "passed": True,
                        "acceptance": checked,
                        "correlation": correlation,
                        "output": record["output"],
                    }
                )
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            series.finish(reservation, True)
            reservation = None
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        if reservation is not None:
            series.finish(reservation, False)
    finally:
        finish_report(series, directory, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--scenario", choices=TASKS, required=True)
    parser.add_argument("--phase", choices=["author", "live"], required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--image", default="sapi-config-lab-n8n:2.41.5")
    args = parser.parse_args(argv)
    series = GeneralizationSeries(args.series_dir)
    report = (
        author(series, args.scenario, image=args.image, upstream=args.upstream)
        if args.phase == "author"
        else live(series, args.scenario, upstream=args.upstream)
    )
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "status": report["status"],
                "error": report.get("error"),
                "budget": report["budget"],
            }
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
