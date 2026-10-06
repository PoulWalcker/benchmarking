"""One experiment run directory: the resources it owns and the report it always writes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.agency import start_bridge, stop_bridge
from sapi_config_lab.execute.host import (
    checked_harbor,
    collect_jobs,
    harbor_run_args,
    image_id,
    pin_base_image,
    run_logged,
    running_containers,
    staging_dir,
)


def fingerprint(path: Path) -> dict[str, str]:
    """Hashes of a file, or of every file under a directory by relative path."""
    if path.is_file():
        return {"": sha256(path)}
    return {str(item.relative_to(path)): sha256(item) for item in sorted(path.rglob("*")) if item.is_file()}


def load_trials(job: Path) -> list[dict]:
    """One row per Harbor trial: its reward, exception and the verifier's report."""
    trials = []
    for path in sorted(job.glob("*/result.json")):
        trial = json.loads(path.read_text())
        report_path = path.parent / "verifier/evaluation/report.json"
        acceptance = json.loads(report_path.read_text()) if report_path.exists() else None
        trials.append(
            {
                "task_name": trial.get("task_name"),
                "rewards": (trial.get("verifier_result") or {}).get("rewards"),
                "exception": trial.get("exception_info"),
                "result_path": str(path),
                "acceptance": acceptance,
            }
        )
    return trials


@dataclass
class Run:
    output: Path
    report: dict[str, Any]
    sources: dict
    prefix: str
    harbor_argv: list[str] = field(default_factory=list)
    identity: str | None = None
    image: str | None = None
    staging: Path | None = None
    bridge_process: subprocess.Popen | None = None
    pinned: dict[str, tuple[Path, dict[str, str]]] = field(default_factory=dict)

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
        self.staging = staging_dir(self.prefix + "-")
        prompts = stage_tasks(self.tasks, mode=mode, image=self.image, scenarios=scenarios, **inputs)
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

    def harbor(self, job: str, tasks: Path, agent: str, *, timeout: float, **arguments: Any) -> tuple[int, list[dict]]:
        """One `harbor run`; its jobs are copied out even when it fails or times out."""
        if self.staging is None:
            raise RuntimeError("No task packages were staged")
        argv = harbor_run_args(self.harbor_argv, tasks, self.staging / "jobs", job, agent, **arguments)
        self.report.setdefault("commands", []).append(argv)
        try:
            exit_code = run_logged(argv, self.output / (job + ".log"), timeout=max(1.0, timeout))
        finally:
            collect_jobs(self.staging, self.output / "jobs")
        return exit_code, load_trials(self.output / "jobs" / job)

    @contextmanager
    def bridge(self, port: int, upstream: str, budget: dict, name: str) -> Iterator[Path]:
        """An Agency bridge enforcing `budget`; yields its audit path and always stops it."""
        budget_path = self.output / "budgets" / (name + ".json")
        audit = self.output / "audits" / (name + ".jsonl")
        budget_path.parent.mkdir(exist_ok=True)
        write_json(budget_path, budget)
        self.pin("budget " + name, budget_path)
        self.bridge_process = start_bridge(port, upstream, audit, budget_path, self.output / (name + "-bridge.log"))
        try:
            yield audit
        finally:
            stop_bridge(self.bridge_process)
            self.bridge_process = None

    def close(self) -> None:
        def cleanup(stage: str, step: Callable[[], Any]) -> None:
            try:
                step()
            except Exception as error:
                self.report.setdefault("cleanup_errors", []).append(
                    {"stage": stage, "error": f"{type(error).__name__}: {error}"}
                )
                self.report["status"] = "failed"

        def collect() -> None:
            if self.staging is None or not self.staging.exists():
                return
            try:
                collect_jobs(self.staging, self.output / "jobs")
            except Exception:
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
        self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(self.output / "report.json", self.report)


def run_experiment(
    output: Path,
    report: dict[str, Any],
    body: Callable[[Run], None],
    *,
    prefix: str,
    classify: Callable[[Exception], str] | None = None,
) -> dict[str, Any]:
    """Create the run directory, call `body(run)`, and always finish the run."""
    output = output.resolve()
    if output.exists():
        raise ValueError("Choose a new report directory; an existing one is never overwritten")
    output.mkdir(parents=True)
    report.setdefault("status", "failed")
    report["started_at"] = datetime.now(timezone.utc).isoformat()
    sources = source_manifest()
    write_json(output / "source-manifest.json", sources)
    report["source_manifest"] = "source-manifest.json"
    run = Run(output, report, sources, prefix)
    try:
        (output / "existing-containers.txt").write_text(running_containers())
        run.harbor_argv, report["harbor_version"] = checked_harbor()
        body(run)
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.TimeoutExpired):
            report["failure_category"] = "timeout_unknown_outcome"
        elif classify is not None:
            report["failure_category"] = classify(error)
    finally:
        run.close()
    return report
