#!/usr/bin/env python3
"""Run bounded real-wrapper Harbor trials after fresh source-matched controls."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from sapi_config_lab.runtime.agency import strict_json
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.experiments.tasks import SCENARIOS, stage_tasks
from sapi_config_lab.experiments.host import harbor_command
from sapi_config_lab.experiments.harbor import IMAGE, load_trials
from sapi_config_lab.experiments.provenance import host_environment, source_manifest
from sapi_config_lab.experiments.replay import load_selection, read_json, require, sha256
from sapi_config_lab.experiments.live_evidence import LIVE_CASES, OPERATIONS, collect_native, reconcile_dispatches
from sapi_config_lab.experiments.expansion import (
    ExpansionSeries,
    RefinementSeries,
    RUNTIME_CAPS,
    select_series_scenarios,
)
from sapi_config_lab.experiments.live_evidence import case_budget
from sapi_config_lab.scenarios import EXPANSION_SCENARIOS, EXTENSION_SCENARIOS
from sapi_config_lab.core.profile import read

ROOT = workspace_root()
BUDGET: dict[str, Any] = {"max_attempts": 8, "operations": OPERATIONS, "model": "gpt-6-astra"}


def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def tree_hashes(path: Path) -> dict[str, str]:
    return {str(item.relative_to(path)): sha256(item) for item in sorted(path.rglob("*")) if item.is_file()}


def validate_control(path: Path, current: dict[str, str], image_id: str) -> dict:
    gate = read_json(path)
    require(
        gate.get("schema") == "sapi-lab-harbor/v1" and gate.get("status") == "passed" and gate.get("mode") == "stub",
        "Live calls require a passing control report",
    )
    require(
        gate.get("source_unchanged") is True and read_json(path.parent / gate["source_manifest"]) == current,
        "Control report does not match current sources",
    )
    require(gate.get("image_id") == image_id and image_id.startswith("sha256:"), "Control report image mismatch")
    require(
        gate.get("transport", {}).get("passed") is True
        and gate.get("oracle", {}).get("passed") is True
        and gate.get("nop", {}).get("passed") is True,
        "Missing fresh transport/oracle/nop controls",
    )
    require(
        gate.get("checks") and all(check.get("passed") is True for check in gate["checks"]),
        "Control checks did not all pass",
    )
    return gate


def wrapper_identity(path: Path, endpoint: str) -> dict:
    evidence = read_json(path)
    require(
        evidence.get("schema") == "sapi-lab-wrapper-identity/v1" and evidence.get("endpoint") == endpoint,
        "Wrapper identity endpoint mismatch",
    )
    require(
        evidence.get("dispatch") == "codex-exec"
        and evidence.get("response_substitution") is False
        and evidence.get("wrapper_retries") == 0
        and evidence.get("model") == BUDGET["model"],
        "Wrapper dispatch inspection is missing or incompatible",
    )
    require(
        evidence.get("provider_internal_retries") in ("unknown", "none", "observed"),
        "Missing lower-layer retry limitation",
    )
    files = evidence.get("files")
    require(isinstance(files, list) and len(files) >= 1, "Missing private wrapper source identity")
    require(len({row["path"] for row in files}) == len(files), "Duplicate wrapper source identity")
    for row in files:
        require(sha256(Path(row["path"])) == row["sha256"], "Wrapper/config identity changed")
    return evidence


def validate_packages(path: Path, submissions: dict, image: str):
    for scenario, selected in submissions.items():
        task = path / scenario
        require(sha256(task / "environment/base.yaml") == selected["sha256"], "Staged submission hash mismatch")
        settings = tomllib.loads((task / "task.toml").read_text())
        require(
            settings["agent"]["timeout_sec"] == 600 and settings["verifier"]["timeout_sec"] == 1800,
            "Staged Harbor timeouts changed",
        )
        require(
            (task / "environment/Dockerfile").read_text().startswith(f"FROM {image}\n"),
            "Task image differs from frozen image",
        )
        require(
            (task / "solution/solve.sh").read_bytes() == (ROOT / "harbor/templates/solve.sh").read_bytes(),
            "Copying agent was changed",
        )


def check_trials(trials: list[dict], submissions: dict, *, mode: str, expected_cases: dict | None = None) -> None:
    names = [trial["task_name"] for trial in trials]
    require(len(names) == len(set(names)) and set(names) <= set(submissions), "Unexpected or duplicate Harbor trial")
    for trial in trials:
        require(
            trial["rewards"] == {"reward": 1.0}
            and not trial["exception"]
            and trial["acceptance"]
            and trial["acceptance"].get("passed") is True,
            "Harbor or independent acceptance failed",
        )
        acceptance = trial["acceptance"]
        scenario = trial["task_name"]
        verifier = Path(trial["result_path"]).parent / "verifier"
        require(
            acceptance.get("mode") == mode and acceptance.get("scenario") == scenario, "Verifier scenario/mode mismatch"
        )
        require(
            acceptance.get("submission_sha256")
            == submissions[scenario]["sha256"]
            == sha256(verifier / "submission.yaml"),
            "Container submission hash mismatch",
        )
        require(
            acceptance.get("cases") and all(row.get("passed") is True for row in acceptance["cases"]),
            "Missing independent case acceptance",
        )
        if mode == "live":
            cohort = LIVE_CASES if expected_cases is None else expected_cases
            require(
                {row["name"] for row in acceptance["cases"]} == cohort[scenario]
                and len(acceptance["cases"]) == len(cohort[scenario]),
                "Required live-case subset changed",
            )


def audit_records(path: Path) -> list[dict]:
    records = [strict_json(line) for line in path.read_text().splitlines()] if path.exists() else []
    require(all(isinstance(row, dict) for row in records), "Audit records must be objects")
    return records


def check_generated_stub_gates(trials: list[dict], scenario: str, expected_case_count: int) -> None:
    """Each independent authoring answer is checked against its own exact bytes."""
    for trial in trials:
        directory = Path(trial["result_path"]).parent
        own_submission = directory / "agent/submission.yaml"
        check_trials(
            [
                {
                    "task_name": trial["scenario"],
                    **{key: trial[key] for key in ("rewards", "exception", "acceptance", "result_path")},
                }
            ],
            {scenario: {"sha256": sha256(own_submission)}},
            mode="stub",
        )
        require(
            len(trial["acceptance"]["cases"]) == expected_case_count, "Generated stub control coverage is incomplete"
        )


def failure_category(trials: list[dict], audit: list[dict], default: str) -> str:
    failed = next((row for row in audit if row.get("event") == "failure"), None)
    if failed:
        return failed["category"]
    for trial in trials:
        if trial.get("exception") or not trial.get("acceptance"):
            return "harbor_environment"
        for row in trial["acceptance"].get("cases", []):
            if row.get("passed"):
                continue
            status = row.get("execution", {}).get("status")
            return {
                "compile_error": "compilation",
                "import_error": "n8n_import",
                "error": "n8n_execution",
                "success": "business_acceptance",
            }.get(status, "yaml_profile")
    return default


def native_attempt_count(output: Path) -> int:
    count = 0
    for path in (output / "jobs").glob("live-*/*/verifier/cases/*/case.json"):
        run = read_json(path)
        count += sum(
            len(records)
            for name, records in run.get("run_data", {}).items()
            if name.startswith("Agency ") or " / Agency " in name
        )
    return count


def finalize_report(report: dict, output: Path, staging: Path | None, adapter, original_sources: dict) -> None:
    """Preserve partial evidence and always emit a failure report on collection errors."""

    def attempt(stage, operation):
        try:
            return operation()
        except Exception as error:
            report.setdefault("collection_errors", []).append(
                {"stage": stage, "error": f"{type(error).__name__}: {error}"}
            )
            report["status"] = "failed"
            report.setdefault("failure_category", "evidence_correlation")
            return None

    def stop_adapter():
        if adapter and adapter.poll() is None:
            adapter.terminate()
            try:
                adapter.wait(timeout=5)
            except subprocess.TimeoutExpired:
                adapter.kill()
                adapter.wait(timeout=5)

    attempt("adapter_stop", stop_adapter)
    if staging:

        def preserve_jobs():
            if (staging / "jobs").exists():
                shutil.copytree(staging / "jobs", output / "jobs", dirs_exist_ok=True)
            return True

        if attempt("preserve_jobs", preserve_jobs):
            attempt("cleanup_owned_staging", lambda: shutil.rmtree(staging))
        else:
            report["retained_staging"] = str(staging)
    audit = attempt("audit", lambda: audit_records(output / "bridge-audit.jsonl"))
    report["audit"] = audit if audit is not None else []
    report["counts"].update(
        {
            "agency_http_attempts": attempt("native_attempt_count", lambda: native_attempt_count(output)),
            "wrapper_attempts": sum(row.get("event") == "dispatch_attempt" for row in audit)
            if audit is not None
            else None,
            "wrapper_completions": sum(row.get("event") == "completion" for row in audit)
            if audit is not None
            else None,
        }
    )
    snapshot = attempt("source_manifest", source_manifest)
    report["source_unchanged"] = snapshot == original_sources
    if snapshot is not None:
        attempt("final_source_manifest", lambda: write_json(output / "final-source-manifest.json", snapshot))
    if not report["source_unchanged"]:
        report.update({"status": "failed", "failure_category": "provenance_preflight"})
    if (output / "existing-containers.txt").exists():

        def preserve_containers():
            after = subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
            (output / "existing-containers-after.txt").write_text(after)
            return set((output / "existing-containers.txt").read_text().splitlines()) <= set(after.splitlines())

        report["existing_container_identities_preserved"] = attempt("container_identity", preserve_containers)
        if report["existing_container_identities_preserved"] is not True:
            report.update({"status": "failed", "failure_category": "provenance_preflight"})
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "report.json", report)


def run_expansion(args) -> int:
    """Replay one frozen scenario with separate fail-closed per-case grants."""
    scenario = args.scenario
    refinement = scenario in EXTENSION_SCENARIOS
    caps = RefinementSeries.runtime_caps[scenario] if refinement else RUNTIME_CAPS[scenario]
    output = (args.report_dir or args.stub_report.resolve().parent / (scenario + "-live")).resolve()
    require(not output.exists(), "Choose a new immutable expansion report directory")
    output.mkdir(parents=True)
    sources = source_manifest()
    write_json(output / "source-manifest.json", sources)
    report: dict[str, Any] = {
        "schema": "sapi-lab-refinement-live/v1" if refinement else "sapi-lab-expansion-live/v1",
        "experiment_kind": "bounded_refinement" if refinement else "bounded_expansion",
        "scenario": scenario,
        "mode": "preflight" if args.preflight_only else "live",
        "status": "failed",
        "source_manifest": "source-manifest.json",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trials": [],
        "not_run": list(caps),
        "correlation": [],
        "phases": [],
        "generation_calls": 0,
        "budget": {
            "max_attempts": sum(caps.values()),
            "cases": caps,
            "counts_are_upper_bounds": refinement,
            "series_max_attempts": None,
        },
        "counts": {
            "agency_http_attempts": 0,
            "wrapper_attempts": 0,
            "wrapper_completions": 0,
            "provider_call_count": None,
        },
        "human_review": "pending"
        if scenario in {"bulletin-market-brief", "priority-support-brief"}
        else "not_applicable",
        "limitations": [
            "Call/time caps are enforced; currency cost and provider-internal retries remain unknown.",
            "Lexical and citation checks are bounded; they are separate from human text review.",
            "Authoring tool restrictions are cooperative prompt/audit controls, not a secure sandbox.",
        ],
    }
    staging = None
    adapter = None
    series = None
    reservation = None
    active_audit = None
    try:
        series = (
            RefinementSeries(args.series_dir)
            if refinement
            else ExpansionSeries(args.series_dir, scenarios=getattr(args, "series_scenarios", None))
        )
        report["series_ledger"] = str(series.path)
        report["series_scenarios"] = list(series.scenarios)
        report["series_ceilings"] = dict(series.ceilings)
        report["budget"]["series_max_attempts"] = series.ceilings["runtime"]
        harbor = harbor_command()
        require(
            subprocess.check_output([*harbor, "--version"], text=True).strip() == "0.21.0", "Expected Harbor 0.21.0"
        )
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
        ).strip()
        gate = validate_control(args.stub_report.resolve(), sources, image_id)
        controlled = {trial["task_name"] for trial in gate["oracle"]["trials"]}
        require(scenario in controlled, "Prepared control report does not cover this scenario")
        report["stub_report_sha256"] = sha256(args.stub_report)
        report["image_id"] = image_id
        shutil.copyfile(args.stub_report, output / "control-report.json")
        before = subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
        (output / "existing-containers.txt").write_text(before)
        submissions = load_selection(args.submissions_manifest, copy_to=output / "selection", scenarios=(scenario,))
        selected = submissions[scenario]
        report["selection_sha256"] = sha256(args.submissions_manifest)
        private_cases = read_json(Path(selected["cases_path"]))
        require(sha256(Path(selected["cases_path"])) == selected["cases_sha256"], "Private fixtures changed")
        require(private_cases[scenario]["live_cases"] == list(caps), "Frozen runtime cohort changed")
        report["private_cases_sha256"] = selected["cases_sha256"]
        if not args.preflight_only:
            require(args.wrapper_evidence is not None, "Expansion requires wrapper identity evidence")
        if args.wrapper_evidence:
            report["wrapper_identity"] = wrapper_identity(args.wrapper_evidence, args.upstream)
            shutil.copyfile(args.wrapper_evidence, output / "wrapper-identity.json")
        frozen_image = "sapi-config-lab-expansion-base:" + image_id.split(":")[-1][:16]
        subprocess.check_call(["docker", "tag", image_id, frozen_image])
        staging = Path(
            tempfile.mkdtemp(
                prefix="sapi-lab-expansion-", dir="/private/tmp" if Path("/private/tmp").exists() else None
            )
        )
        stage_tasks(
            staging / "tasks", mode="replay", image=frozen_image, submissions=submissions, scenarios=(scenario,)
        )
        validate_packages(staging / "tasks", submissions, frozen_image)
        shutil.copytree(staging / "tasks", output / "task-packages")
        package_hashes = tree_hashes(staging / "tasks")
        selection_hashes = tree_hashes(output / "selection")
        write_json(output / "task-package-hashes.json", package_hashes)

        def freeze(phase):
            require(source_manifest() == sources, "Expansion sources changed")
            require(
                tree_hashes(staging / "tasks") == package_hashes == tree_hashes(output / "task-packages"),
                "Expansion task package changed",
            )
            require(tree_hashes(output / "selection") == selection_hashes, "Preserved selection changed")
            require(sha256(args.submissions_manifest) == report["selection_sha256"], "Selection manifest changed")
            require(
                load_selection(args.submissions_manifest, scenarios=(scenario,)) == submissions,
                "Selected evidence changed",
            )
            require(
                subprocess.check_output(
                    ["docker", "image", "inspect", frozen_image, "--format", "{{.Id}}"], text=True
                ).strip()
                == image_id,
                "Expansion image changed",
            )
            if args.wrapper_evidence:
                require(
                    wrapper_identity(args.wrapper_evidence, args.upstream) == report.get("wrapper_identity"),
                    "Wrapper identity changed",
                )
            report["phases"].append({"phase": phase, "source_unchanged": True, "packages_unchanged": True})

        # Selection independently rechecks both generated stub gates and their exact bytes.
        selection = read_json(args.submissions_manifest)
        generated = read_json(Path(selection["source_report"]["path"]))
        expected_count = len(private_cases[scenario]["positive"]) + len(private_cases[scenario]["negative"]) + 4
        check_generated_stub_gates(generated["trials"], scenario, expected_count)
        report["preflight"] = {
            "passed": True,
            "source": f"{series.authoring_attempts} preserved generated stub trials",
            "source_report_sha256": selection["source_report"]["sha256"],
        }
        freeze("before-live")
        if args.preflight_only:
            report["status"] = "passed"
        else:
            (output / "case-budgets").mkdir()
            (output / "case-audits").mkdir()
            for case_name, call_cap in caps.items():
                freeze("before-" + case_name)
                config = copy.deepcopy(read(Path(selected["path"])))
                case = next(row for row in private_cases[scenario]["positive"] if row["name"] == case_name)
                config["workflow"]["inputs"] = case["inputs"]
                budget = case_budget(scenario, case_name, config, cases=private_cases)
                require(budget["max_attempts"] == call_cap, "Case grant differs from frozen call count")
                budget["expires_at"] = time.time() + 600
                budget_path = output / "case-budgets" / (case_name + ".json")
                write_json(budget_path, budget)
                active_audit = output / "case-audits" / (case_name + ".jsonl")
                reservation = series.reserve(scenario, "runtime", case_name, output / "report.json")
                with (output / (case_name + "-bridge.log")).open("w") as log:
                    adapter = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "sapi_config_lab.runtime.agency",
                            "--port",
                            str(args.bridge_port),
                            "--upstream",
                            args.upstream,
                            "--timeout",
                            "185",
                            "--audit",
                            str(active_audit),
                            "--budget",
                            str(budget_path),
                        ],
                        cwd=ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                for _ in range(50):
                    require(adapter.poll() is None, "Case adapter exited before readiness")
                    try:
                        with urlopen(f"http://127.0.0.1:{args.bridge_port}/health", timeout=1) as response:
                            if json.load(response).get("service") == "sapi-lab-agency-adapter":
                                break
                    except URLError, TimeoutError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Case adapter readiness timeout")
                time.sleep(0.1)
                require(adapter.poll() is None, "Case adapter port is already in use")
                name = "live-" + scenario + "-" + case_name
                command = [
                    *harbor,
                    "run",
                    "--path",
                    str(staging / "tasks" / scenario),
                    "--agent",
                    "oracle",
                    "--n-concurrent",
                    "1",
                    "--max-retries",
                    "0",
                    "--jobs-dir",
                    str(staging / "jobs"),
                    "--job-name",
                    name,
                    "--verifier-env",
                    "SAPI_LLM_MODE=live",
                    "--verifier-env",
                    "SAPI_CASE_NAME=" + case_name,
                    "--verifier-env",
                    f"SAPI_BRIDGE_URL=http://host.docker.internal:{args.bridge_port}",
                    "--force-build",
                ]
                report.setdefault("commands", []).append(command)
                print(f"Live expansion: {scenario}/{case_name}; max {call_cap} outgoing attempts", flush=True)
                try:
                    with (output / (name + ".log")).open("w") as log:
                        completed = subprocess.run(
                            command,
                            cwd=ROOT,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=max(1, budget["expires_at"] - time.time()),
                        )
                finally:
                    if (staging / "jobs").exists():
                        shutil.copytree(staging / "jobs", output / "jobs", dirs_exist_ok=True)
                    if adapter.poll() is None:
                        adapter.terminate()
                        try:
                            adapter.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            adapter.kill()
                            adapter.wait(timeout=5)
                trials = load_trials(output / "jobs" / name)
                report["trials"].extend(trials)
                require(completed.returncode == 0 and len(trials) == 1, "Expansion Harbor case failed")
                check_trials(trials, submissions, mode="live", expected_cases={scenario: {case_name}})
                native = collect_native(
                    trials, submissions, expected_cases={scenario: {case_name}}, cases=private_cases
                )
                audit = audit_records(active_audit)
                correlation = reconcile_dispatches(native, audit, budget["model"])
                require(
                    1 <= len(correlation) <= call_cap if refinement else len(correlation) == call_cap,
                    "Unexpected case occurrence count",
                )
                require(read_json(budget_path) == budget, "Case admission grant changed")
                freeze("after-" + case_name)
                report["correlation"].extend(correlation)
                report["not_run"].remove(case_name)
                series.finish(reservation, True)
                reservation = None
            require(
                len(report["correlation"]) <= report["budget"]["max_attempts"]
                if refinement
                else len(report["correlation"]) == report["budget"]["max_attempts"],
                "Incomplete or over-budget runtime cohort",
            )
            write_json(output / "correlation.json", report["correlation"])
            report["status"] = "passed"
    except Exception as error:
        if reservation is not None and series is not None:
            series.finish(reservation, False)
        report["error"] = f"{type(error).__name__}: {error}"
        report["failure_category"] = (
            "timeout_unknown_outcome" if isinstance(error, subprocess.TimeoutExpired) else "expansion_gate"
        )
    finally:
        combined = []
        for path in (output / "case-audits").glob("*.jsonl"):
            try:
                combined.extend(audit_records(path))
            except Exception as error:
                report["status"] = "failed"
                report.setdefault("collection_errors", []).append({"stage": "case_audit", "error": str(error)})
        try:
            (output / "bridge-audit.jsonl").write_text("".join(json.dumps(row) + "\n" for row in combined))
        except Exception as error:
            report["status"] = "failed"
            report.setdefault("collection_errors", []).append(
                {"stage": "bridge_audit_aggregation", "error": str(error)}
            )
        finally:
            finalize_report(report, output, staging, adapter, sources)
    print(
        json.dumps({"status": report["status"], "report": str(output / "report.json"), "counts": report["counts"]}),
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stub-report", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--submissions-manifest", "--submission-manifest", dest="submissions_manifest", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--wrapper-evidence", type=Path)
    parser.add_argument("--upstream", default="http://127.0.0.1:8765/run")
    parser.add_argument("--bridge-port", type=int, default=18765)
    parser.add_argument("--scenario", choices=tuple({**EXPANSION_SCENARIOS, **EXTENSION_SCENARIOS}))
    parser.add_argument("--series-dir", type=Path)
    parser.add_argument(
        "--series-scenario",
        action="append",
        help="Freeze this ordered series subset; repeat per scenario, default all four",
    )
    args = parser.parse_args(argv)
    if args.scenario in EXTENSION_SCENARIOS:
        if args.series_scenario or not args.submissions_manifest or not args.series_dir:
            parser.error("Refinement requires --submissions-manifest and --series-dir, without expansion cohort flags")
        return run_expansion(args)
    if args.scenario:
        try:
            args.series_scenarios = select_series_scenarios(
                tuple(args.series_scenario) if args.series_scenario else None
            )
            if args.scenario not in args.series_scenarios:
                raise ValueError("Task scenario is not selected for this expansion series")
        except ValueError as error:
            parser.error(str(error))
        if not args.submissions_manifest or not args.series_dir:
            parser.error("Expansion requires --submissions-manifest and --series-dir")
        return run_expansion(args)
    if args.series_scenario:
        parser.error("--series-scenario requires an expansion --scenario")
    if args.preflight_only and not args.submissions_manifest:
        parser.error("--preflight-only requires --submissions-manifest")
    output = (args.report_dir or args.stub_report.resolve().parent / "live").resolve()
    if output.exists():
        parser.error("Choose a new report directory; every existing output directory is immutable")
    output.mkdir(parents=True, exist_ok=False)
    original_sources = source_manifest()
    write_json(output / "source-manifest.json", original_sources)
    report: dict[str, Any] = {
        "schema": "sapi-lab-generated-live/v1" if args.submissions_manifest else "sapi-lab-live/v2",
        "experiment_kind": "frozen_generated_replay" if args.submissions_manifest else "reference_live",
        "mode": "preflight" if args.preflight_only else "live",
        "status": "failed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "host_environment": host_environment(),
        "stub_report": str(args.stub_report.resolve()),
        "stub_report_sha256": None,
        "source_manifest": "source-manifest.json",
        "n8n_version": "2.41.5",
        "generation_calls": 0,
        "budget": BUDGET,
        "trials": [],
        "not_run": list(SCENARIOS),
        "phases": [],
        "audit": [],
        "counts": {
            "agency_http_attempts": 0,
            "wrapper_attempts": 0,
            "wrapper_completions": 0,
            "provider_call_count": None,
        },
        "limitations": [
            "Frozen historical generated replay is not fresh generation or held-out generalization.",
            "The wrapper is trusted; provider-internal requests/retries and currency cost are not established.",
            "CLI token telemetry is incomplete and is not a cost estimate.",
            "Timeout cancellation is not propagated; an upstream model outcome can remain unknown.",
            "Quotation and factual-anchor checks do not establish exhaustive semantic correctness.",
        ],
    }
    adapter = None
    staging = None
    current_stage = "provenance_preflight"
    frozen_image = None
    try:
        report["stub_report_sha256"] = sha256(args.stub_report)
        harbor = harbor_command()
        version = subprocess.check_output([*harbor, "--version"], text=True).strip()
        require(version == "0.21.0", "Expected Harbor 0.21.0")
        report["harbor_version"] = version
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"], text=True
        ).strip()
        gate = validate_control(args.stub_report.resolve(), original_sources, image_id)
        report["image_id"] = image_id
        shutil.copyfile(args.stub_report, output / "control-report.json")
        shutil.copyfile(args.stub_report.parent / gate["source_manifest"], output / "control-source-manifest.json")
        shutil.copyfile(args.stub_report.parent / "runtime-versions.txt", output / "runtime-versions.txt")
        before = subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
        (output / "existing-containers.txt").write_text(before)
        if args.submissions_manifest:
            submissions = load_selection(args.submissions_manifest, copy_to=output / "selection")
            report["selection_sha256"] = sha256(args.submissions_manifest)
        else:
            submissions = {
                scenario: {"path": ROOT / "configs" / filename, "sha256": sha256(ROOT / "configs" / filename)}
                for scenario, filename in SCENARIOS.items()
            }
        if args.wrapper_evidence:
            report["wrapper_identity"] = wrapper_identity(args.wrapper_evidence, args.upstream)
            shutil.copyfile(args.wrapper_evidence, output / "wrapper-identity.json")
        if args.submissions_manifest and not args.preflight_only:
            require(
                args.wrapper_evidence is not None,
                "Generated live dispatch requires read-only wrapper inspection evidence",
            )
        frozen_image = "sapi-config-lab-live-base:" + image_id.split(":")[-1][:16]
        subprocess.check_call(["docker", "tag", image_id, frozen_image])
        report["frozen_image"] = frozen_image
        staging = Path(
            tempfile.mkdtemp(prefix="sapi-lab-live-", dir="/private/tmp" if Path("/private/tmp").exists() else None)
        )
        stage_tasks(
            staging / "tasks",
            mode="replay" if args.submissions_manifest else "oracle",
            image=frozen_image,
            submissions=submissions if args.submissions_manifest else None,
        )
        validate_packages(staging / "tasks", submissions, frozen_image)
        shutil.copytree(staging / "tasks", output / "task-packages")
        selection_hashes = tree_hashes(output / "selection") if args.submissions_manifest else {}
        package_hashes = tree_hashes(staging / "tasks")
        write_json(output / "task-package-hashes.json", package_hashes)
        write_json(output / "budget.json", BUDGET)

        def freeze(phase):
            snapshot = source_manifest()
            write_json(output / (phase + "-sources.json"), snapshot)
            require(snapshot == original_sources, "Public sources changed during experiment")
            require(
                tree_hashes(staging / "tasks") == package_hashes == tree_hashes(output / "task-packages"),
                "Task package changed during experiment",
            )
            require(
                subprocess.check_output(
                    ["docker", "image", "inspect", frozen_image, "--format", "{{.Id}}"], text=True
                ).strip()
                == image_id,
                "Frozen image changed",
            )
            require(read_json(output / "budget.json") == BUDGET, "Outgoing budget file changed")
            if args.submissions_manifest:
                require(tree_hashes(output / "selection") == selection_hashes, "Preserved selection artifacts changed")
                require(sha256(args.submissions_manifest) == report["selection_sha256"], "Selection manifest changed")
                require(load_selection(args.submissions_manifest) == submissions, "Selected source changed")
            if args.wrapper_evidence:
                require(
                    wrapper_identity(args.wrapper_evidence, args.upstream) == report["wrapper_identity"],
                    "Wrapper inspection changed",
                )
            report["phases"].append(
                {"phase": phase, "source_unchanged": True, "packages_unchanged": True, "image_id": image_id}
            )

        def run_harbor(name: str, task_path: Path, mode: str, timeout: float = 2400):
            command = [
                *harbor,
                "run",
                "--path",
                str(task_path),
                "--agent",
                "oracle",
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--jobs-dir",
                str(staging / "jobs"),
                "--job-name",
                name,
                "--verifier-env",
                "SAPI_LLM_MODE=" + mode,
                "--force-build",
            ]
            if mode == "live":
                command += ["--verifier-env", f"SAPI_BRIDGE_URL=http://host.docker.internal:{args.bridge_port}"]
            report.setdefault("commands", []).append(command)
            try:
                with (output / (name + ".log")).open("w") as log:
                    completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            finally:
                if (staging / "jobs").exists():
                    shutil.copytree(staging / "jobs", output / "jobs", dirs_exist_ok=True)
            trials = load_trials(output / "jobs" / name)
            return completed.returncode, trials

        freeze("before-stub")
        current_stage = "harbor_environment"
        print(f"Generated replay unpaid verification; logs: {output}", flush=True)
        rc, stub_trials = run_harbor("stub-replay", staging / "tasks", "stub")
        report["preflight"] = {"harbor_exit_code": rc, "trials": stub_trials, "passed": False}
        freeze("after-stub")
        require(
            rc == 0 and len(stub_trials) == 3 and {trial["task_name"] for trial in stub_trials} == set(SCENARIOS),
            "Replay stub Harbor trials failed",
        )
        check_trials(stub_trials, submissions, mode="stub")
        rows = [row for trial in stub_trials for row in trial["acceptance"]["cases"]]
        executions = sum(bool(row.get("execution_id")) for row in rows)
        rejections = sum(row.get("execution", {}).get("status") == "compile_error" for row in rows)
        require(
            (len(rows), executions, rejections) == (32, 23, 9),
            "Full generated replay control coverage differs from 32/23/9",
        )
        report["preflight"].update(
            {"passed": True, "cases": len(rows), "n8n_executions": executions, "compile_rejections": rejections}
        )
        if args.preflight_only:
            report["status"] = "passed"
            report["not_run"] = list(SCENARIOS)
        else:
            current_stage = "provenance_preflight"
            freeze("before-live")
            print(
                json.dumps(
                    {
                        "experiment_kind": report["experiment_kind"],
                        "selected_submissions": {name: item["sha256"] for name, item in submissions.items()},
                        "live_cases": 7,
                        "maximum_outgoing_attempts": 8,
                        "operation_limits": OPERATIONS,
                        "generation_calls": 0,
                        "wrapper_identity_verified": bool(args.wrapper_evidence),
                        "provider_call_count": None,
                    }
                ),
                flush=True,
            )
            with (output / "bridge.log").open("w") as bridge_log:
                adapter = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "sapi_config_lab.runtime.agency",
                        "--port",
                        str(args.bridge_port),
                        "--upstream",
                        args.upstream,
                        "--timeout",
                        "185",
                        "--audit",
                        str(output / "bridge-audit.jsonl"),
                        "--budget",
                        str(output / "budget.json"),
                    ],
                    cwd=ROOT,
                    stdout=bridge_log,
                    stderr=subprocess.STDOUT,
                )
            for _ in range(50):
                require(adapter.poll() is None, "Local adapter exited before readiness")
                try:
                    with urlopen(f"http://127.0.0.1:{args.bridge_port}/health", timeout=1) as response:
                        if json.load(response).get("service") == "sapi-lab-agency-adapter":
                            break
                except URLError, TimeoutError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Local adapter readiness timeout")
            time.sleep(0.1)
            require(adapter.poll() is None, "Adapter port is already in use")
            live_deadline = time.monotonic() + 2400
            for scenario in SCENARIOS:
                freeze("before-live-" + scenario)
                audit = audit_records(output / "bridge-audit.jsonl")
                require(not any(row.get("event") == "failure" for row in audit), "Outgoing failure latch is set")
                current_stage = "harbor_environment"
                remaining = live_deadline - time.monotonic()
                require(remaining > 0, "Live Harbor series deadline expired")
                print(f"Live frozen replay: {scenario}", flush=True)
                report["not_run"].remove(scenario)
                rc, trials = run_harbor("live-" + scenario, staging / "tasks" / scenario, "live", remaining)
                report["trials"].extend(trials)
                freeze("after-live-" + scenario)
                require(rc == 0 and len(trials) == 1 and trials[0]["task_name"] == scenario, "Live Harbor trial failed")
                check_trials(trials, submissions, mode="live")
                current_stage = "evidence_correlation"
                native = collect_native(report["trials"], submissions)
                report["correlation"] = reconcile_dispatches(
                    native, audit_records(output / "bridge-audit.jsonl"), BUDGET["model"]
                )
                write_json(output / "correlation.json", report["correlation"])
            require(len(report["trials"]) == 3 and len(report["correlation"]) == 8, "Incomplete live series")
            counts = {
                operation: sum(row["operation"] == operation for row in report["correlation"])
                for operation in OPERATIONS
            }
            require(
                counts == OPERATIONS and not any(row["scenario"] == "invoice-total" for row in report["correlation"]),
                "Unexpected live operation counts",
            )
            report["operation_counts"] = counts
            freeze("after-live")
            report["status"] = "passed"
    except Exception as error:
        try:
            audit = audit_records(output / "bridge-audit.jsonl")
        except Exception as collection_error:
            audit = []
            report.setdefault("collection_errors", []).append({"stage": "audit", "error": str(collection_error)})
        report["failure_category"] = (
            "timeout_unknown_outcome"
            if isinstance(error, subprocess.TimeoutExpired)
            else failure_category(
                report["trials"] + report.get("preflight", {}).get("trials", []), audit, current_stage
            )
        )
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        finalize_report(report, output, staging, adapter, original_sources)
    print(
        json.dumps({"status": report["status"], "report": str(output / "report.json"), "counts": report["counts"]}),
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
