"""One experiment run directory: the resources it owns and the report it always writes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from sapi_config_lab.coordinate.evaluation import trial_result
from sapi_config_lab.coordinate.packages import UPLOAD_ONLY_AGENTS, job_seconds, stage_tasks, verifier_bounds
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.providers import ENVIRONMENTS, EVALUATORS
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.agency import start_bridge, stop_bridge
from sapi_config_lab.execute.host import (
    HostConfig,
    checked_harbor,
    collect_jobs,
    harbor_run_args,
    image_id,
    pin_base_image,
    run_logged,
    running_containers,
    staging_dir,
)
from sapi_config_lab.execute.hosting import TrialHost
from sapi_config_lab.harbor_integration.runner import job_args, run_job
from sapi_config_lab.paths import CATALOG


def fingerprint(path: Path) -> dict[str, str]:
    """Hashes of a file, or of every file under a directory by relative path."""
    if path.is_file():
        return {"": sha256(path)}
    return {str(item.relative_to(path)): sha256(item) for item in sorted(path.rglob("*")) if item.is_file()}


def progress(message: str) -> None:
    """Human progress on stderr; stdout stays machine-readable."""
    print(message, file=sys.stderr, flush=True)


def trial_seconds(trial: dict) -> int | None:
    """Wall-clock seconds Harbor recorded for one trial, if it recorded both ends."""
    path = Path(trial["result_path"])
    if not path.exists():
        return None
    recorded = json.loads(path.read_text())
    try:
        started, finished = (datetime.fromisoformat(recorded[key]) for key in ("started_at", "finished_at"))
    except KeyError, TypeError, ValueError:
        return None
    return round((finished - started).total_seconds())


def load_trials(job: Path, hosted: dict[str, Path] | None = None) -> list[dict]:
    """One row per Harbor trial; a hosted trial's report is its host's record, never the container's copy."""
    trials = []
    for directory in sorted(path for path in job.glob("*") if path.is_dir()):
        path = directory / "result.json"
        trial = json.loads(path.read_text()) if path.exists() else {}
        config = directory / "config.json"
        task = json.loads(config.read_text()).get("task", {}) if config.exists() else {}
        name = trial.get("task_name") or (Path(task["path"]).name if task.get("path") else None)
        record = (hosted or {}).get(name, path.parent / "verifier") if name else path.parent / "verifier"
        report_path = record / "evaluation/report.json"
        row = {
            "task_name": name,
            "trial_id": trial.get("id") or directory.name,
            "trial_path": str(directory.relative_to(job.parent.parent)),
            "evidence_path": str(directory.relative_to(job.parent.parent) / "verifier"),
            "partial": not path.exists(),
            "rewards": (trial.get("verifier_result") or {}).get("rewards"),
            "exception": trial.get("exception_info"),
            "result_path": str(path),
            "evaluation_path": str(report_path),
            "acceptance": json.loads(report_path.read_text()) if report_path.exists() else None,
        }
        trials.append({**row, "result": trial_result(row)})
    return trials


@dataclass(frozen=True)
class Hosting:
    """How a job's hosted tasks are served: runtime model mode, bridge and evaluator."""

    llm_mode: str
    evaluate: Callable[[str, Path], dict]
    bridge_url: str | None = None


def task_dirs(tasks: Path) -> list[Path]:
    return [tasks] if (tasks / "task.toml").is_file() else sorted(path for path in tasks.iterdir() if path.is_dir())


@dataclass
class Run:
    output: Path
    report: dict[str, Any]
    sources: dict
    prefix: str
    host: HostConfig = field(default_factory=HostConfig.from_environment)
    harbor_argv: list[str] = field(default_factory=list)
    identity: str | None = None
    image: str | None = None
    staging: Path | None = None
    bridge_process: subprocess.Popen | None = None
    pinned: dict[str, tuple[Path, dict[str, str]]] = field(default_factory=dict)
    bounds: dict[str, int] = field(default_factory=dict)

    @property
    def tasks(self) -> Path:
        if self.staging is None:
            raise RuntimeError("No task packages were staged")
        return self.staging / "tasks"

    def use_image(self, tag: str) -> str:
        """Pin the content identity behind `tag` under a run-specific tag."""
        self.identity = image_id(tag)
        self.image = pin_base_image(self.identity, self.prefix)
        self.report.update(image_id=self.identity, frozen_image=self.image)
        return self.identity

    def stage(self, mode: str, scenarios: tuple[str, ...], **inputs: Any) -> dict:
        """Stage task packages from the pinned image and keep a copy beside the report."""
        if self.image is None:
            raise RuntimeError("Pin an image before staging task packages")
        self.staging = staging_dir(self.prefix + "-", self.host)
        prompts = stage_tasks(self.tasks, mode=mode, image=self.image, scenarios=scenarios, **inputs)
        self.bounds = verifier_bounds(scenarios, mode, inputs.get("submissions"), inputs.get("cases"))
        shutil.copytree(self.tasks, self.output / "task-packages")
        self.pin("task-packages", self.output / "task-packages")
        write_json(self.output / "task-package-hashes.json", self.pinned["task-packages"][1])
        return prompts

    def pin(self, name: str, path: Path) -> None:
        """Record bytes that must not change for the rest of the run."""
        self.pinned[name] = (path, fingerprint(path))

    def check(self, phase: str) -> None:
        """Sources, image, staged packages and every pinned input are as recorded."""
        if source_manifest() != self.sources:
            raise RuntimeError(f"Sources changed ({phase})")
        if self.identity is not None and image_id(self.image or "") != self.identity:
            raise RuntimeError(f"Pinned image changed ({phase})")
        for name, (path, recorded) in self.pinned.items():
            if fingerprint(path) != recorded:
                raise RuntimeError(f"Pinned {name} changed ({phase})")
        if self.staging is not None and fingerprint(self.tasks) != self.pinned["task-packages"][1]:
            raise RuntimeError(f"Staged task packages changed ({phase})")
        self.report.setdefault("phases", []).append({"phase": phase, "unchanged": True})

    def harbor(
        self,
        job: str,
        tasks: Path,
        agent: str,
        *,
        hosting: Hosting | None = None,
        admission: bool = False,
        **arguments: Any,
    ) -> tuple[int, list[dict]]:
        """Submit native packages durably; retain bounded staging for legacy callers."""
        if self.staging is None:
            raise RuntimeError("No task packages were staged")
        if agent not in UPLOAD_ONLY_AGENTS:
            raise RuntimeError(f"Agent {agent} may run commands beside the shared verifier environment")
        names = [task.name for task in task_dirs(tasks)]
        if not admission and any(SCENARIOS[name].hosted for name in names) and int(arguments.get("attempts") or 1) != 1:
            raise ValueError("Hosted execution requires exactly one attempt per fresh environment")
        if not set(names) <= set(self.bounds):
            raise RuntimeError(
                "Tasks this run did not stage have no time bound: " + ", ".join(sorted(set(names) - set(self.bounds)))
            )
        if admission:
            arguments["verifier_env"] = [*arguments.get("verifier_env", []), "SAPI_HOSTED_ADMISSION=1"]
        native = all((task / "tests/benchmark.json").is_file() for task in task_dirs(tasks))
        if native:
            self.check("before " + job)
            dispatched = self.report.setdefault("harbor_jobs", {})
            if job in dispatched:
                raise ValueError("A Harbor job cannot be dispatched twice")
            jobs = self.output / "jobs"
            dispatched[job] = {"path": str(Path("jobs") / job), "status": "dispatched"}
            argv = job_args(self.harbor_argv, tasks, jobs, job, agent, **arguments)
            self.report.setdefault("commands", []).append(argv)
            try:
                code = run_job(self.harbor_argv, tasks, jobs, job, agent, self.output / (job + ".log"), **arguments)
                dispatched[job]["exit_code"] = code
                dispatched[job]["status"] = "finished"
                return code, load_trials(jobs / job)
            finally:
                dispatched[job]["trials"] = load_trials(jobs / job)
        timeout = job_seconds({name: self.bounds[name] for name in names}, int(arguments.get("attempts") or 1))
        self.report.setdefault("harbor_timeouts", {})[job] = timeout
        records = self.output / "environments" / job
        with self.hosted(job, tasks, records, hosting, admission=admission) as (job_tasks, hosted):
            argv = harbor_run_args(self.harbor_argv, job_tasks, self.staging / "jobs", job, agent, **arguments)
            self.report.setdefault("commands", []).append(argv)
            try:
                exit_code = run_logged(argv, self.output / (job + ".log"), timeout=timeout)
            finally:
                collect_jobs(self.staging, self.output / "jobs")
        return exit_code, load_trials(self.output / "jobs" / job, hosted)

    @contextmanager
    def hosted(
        self, job: str, tasks: Path, records: Path, hosting: Hosting | None, *, admission: bool = False
    ) -> Iterator[tuple[Path, dict[str, Path]]]:
        """Serve each hosted task for one job; yields the task path Harbor runs and each host's record.

        Credentials go into a per-job copy so pinned packages never change, and must not leak into anything persisted.
        """
        hosted = [task for task in task_dirs(tasks) if (task / "tests/environment.json").is_file()]
        if not hosted or admission:
            yield tasks, {}
            return
        if hosting is None:
            raise RuntimeError("Hosted tasks need a host environment")
        assert self.staging is not None
        copy = self.staging / "hosted" / job / (tasks.name if tasks in hosted else "tasks")
        shutil.copytree(tasks, copy)
        hosts: list[TrialHost] = []
        try:
            with ExitStack() as stack:
                for task in hosted:
                    environment = json.loads((task / "tests/environment.json").read_text())
                    scenario = SCENARIOS[environment["scenario"]]
                    provider = ENVIRONMENTS[scenario.environment]
                    host = TrialHost(
                        partial(provider.start, scenario, environment["seed"], self.host),
                        records / scenario.name,
                        scenario=scenario.name,
                        seed=environment["seed"],
                        artifact=scenario.artifact,
                        llm_mode=hosting.llm_mode,
                        bridge_url=hosting.bridge_url,
                        evaluate=partial(hosting.evaluate, scenario.name),
                        host=self.host,
                    )
                    stack.callback(host.close)
                    hosts.append(host)
                    target = copy if tasks in hosted else copy / task.name
                    write_json(
                        target / "tests/connection.json",
                        {
                            **host.connection,
                            "evaluation_seconds": EVALUATORS[scenario.evaluator].timeout_seconds,
                        },
                    )
                yield copy, {host.record.name: host.record for host in hosts}
        finally:
            shutil.rmtree(copy)
            credentials = [value for host in hosts for value in host.credentials()]
            persisted = [self.output / "jobs" / job, records, self.output / (job + ".log")]
            leaked = [
                str(path)
                for root in persisted
                for path in ([root] if root.is_file() else root.rglob("*") if root.exists() else [])
                if path.is_file() and any(value.encode() in path.read_bytes() for value in credentials)
            ]
            if leaked:
                raise RuntimeError("Ephemeral credentials appeared in persisted artifacts: " + ", ".join(leaked))

    @contextmanager
    def bridge(self, budget: dict, name: str, *, bindings: Path = CATALOG, reject_tool_use=False) -> Iterator[Path]:
        """An Agency bridge enforcing `budget`; yields its audit path and always stops it."""
        budget_path = self.output / "budgets" / (name + ".json")
        audit = self.output / "audits" / (name + ".jsonl")
        budget_path.parent.mkdir(exist_ok=True)
        write_json(budget_path, budget)
        self.pin("budget " + name, budget_path)
        self.bridge_process = start_bridge(
            self.host.listen_host,
            self.host.bridge_port,
            self.host.wrapper_url,
            audit,
            budget_path,
            self.output / (name + "-bridge.log"),
            bindings,
            reject_tool_use,
        )
        try:
            yield audit
        finally:
            stop_bridge(self.bridge_process)
            self.bridge_process = None

    def close(self) -> None:
        def cleanup(stage: str, step: Callable[[], Any]) -> None:
            try:
                step()
            except Exception as error:  # Every cleanup step runs and is reported
                self.report.setdefault("cleanup_errors", []).append(
                    {"stage": stage, "error": f"{type(error).__name__}: {error}"}
                )
                self.report["status"] = "failed"

        def collect() -> None:
            if self.staging is None or not self.staging.exists():
                return
            try:
                if (self.staging / "jobs").exists():
                    collect_jobs(self.staging, self.output / "jobs")
            except OSError:
                self.report["retained_staging"] = str(self.staging)
                raise
            shutil.rmtree(self.staging)

        def containers() -> None:
            before = (self.output / "existing-containers.txt").read_text().splitlines()
            after = running_containers()
            (self.output / "existing-containers-after.txt").write_text(after)
            preserved = set(before) <= set(after.splitlines())
            self.report["existing_container_identities_preserved"] = preserved
            if not preserved:
                raise RuntimeError("A container running before the experiment is gone")

        def unchanged_sources() -> None:
            self.report["source_unchanged"] = source_manifest() == self.sources

        self.report["source_unchanged"] = False
        cleanup("bridge_stop", lambda: stop_bridge(self.bridge_process))
        cleanup("source_manifest", unchanged_sources)
        cleanup("final_check", lambda: self.check("final"))
        cleanup("collect_jobs", collect)
        cleanup("containers", containers)
        self.report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(self.output / "report.json", self.report)


def run_experiment(
    output: Path,
    report: dict[str, Any],
    body: Callable[[Run], None],
    *,
    prefix: str,
    classify: Callable[[Exception], str] | None = None,
    host: HostConfig | None = None,
) -> dict[str, Any]:
    """Create the run directory, call `body(run)`, and always finish the run."""
    output = output.resolve()
    if output.exists():
        raise ValueError("Choose a new report directory; an existing one is never overwritten")
    output.mkdir(parents=True)
    report.setdefault("status", "failed")
    report["started_at"] = datetime.now(UTC).isoformat()
    sources = source_manifest()
    write_json(output / "source-manifest.json", sources)
    report["source_manifest"] = "source-manifest.json"
    run = Run(output, report, sources, prefix, host or HostConfig.from_environment())
    try:
        (output / "existing-containers.txt").write_text(running_containers())
        run.harbor_argv, report["harbor_version"] = checked_harbor()
        body(run)
    except Exception as error:  # A run always ends with a written report
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        progress(f"failed: {report['error']}")
        if isinstance(error, subprocess.TimeoutExpired):
            report["failure_category"] = "timeout_unknown_outcome"
        elif classify is not None:
            report["failure_category"] = classify(error)
    finally:
        progress("finalizing: cleanup, final source check and report")
        run.close()
    return report
