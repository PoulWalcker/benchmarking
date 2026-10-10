"""One experiment run directory: the resources it owns and the report it always writes."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from sapi_config_lab.coordinate.native_tasks import image_tags, public_sources
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evaluate.records import load_trials as load_trials
from sapi_config_lab.evaluate.records import trial_seconds as trial_seconds
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.execute.agency import start_bridge, stop_bridge
from sapi_config_lab.execute.host import (
    HostConfig,
    checked_harbor,
    docker_preflight,
    image_id,
    log_tail,
    pin_base_image,
    run_logged,
    running_containers,
)
from sapi_config_lab.harbor_integration.runner import UPLOAD_ONLY_AGENTS, job_args, run_job
from sapi_config_lab.paths import workspace_root


def fingerprint(path: Path) -> dict[str, str]:
    """Hashes of a file, or of every file under a directory by relative path."""
    if path.is_file():
        return {"": sha256(path)}
    return {str(item.relative_to(path)): sha256(item) for item in sorted(path.rglob("*")) if item.is_file()}


def progress(message: str) -> None:
    """Human progress on stderr; stdout stays machine-readable."""
    print(message, file=sys.stderr, flush=True)


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
    bridge_process: subprocess.Popen | None = None
    pinned: dict[str, tuple[Path, dict[str, str]]] = field(default_factory=dict)
    native_tasks: tuple[Path, ...] = ()
    native_images: dict[str, str] = field(default_factory=dict)

    @contextmanager
    def step(self, stage: str, log: str | None = None) -> Iterator[Path | None]:
        """Record the innermost failed or interrupted stage without converting its exception."""
        path = self.output / f"{log}.log" if log is not None else None
        if path is not None:
            self.report.setdefault("logs", {})[log] = path.name
        try:
            yield path
        except Exception as error:
            if "failure_stage" not in self.report:
                self.report["failure_stage"] = stage
                self.report["log"] = path.name if path is not None else None
                self.report["log_tail"] = log_tail(
                    path, stderr=error.stderr if isinstance(error, subprocess.CalledProcessError) else None
                )
            raise
        except KeyboardInterrupt:
            self.report.setdefault("interrupted_stage", stage)
            raise

    def use_native_tasks(self, tasks: tuple[Path, ...], *, build: bool = False) -> None:
        """Pin direct task sources and the source-verified local images that Harbor will consume."""
        with self.step("native build" if build else "native images", log="native-build" if build else None):
            if not tasks or len(set(tasks)) != len(tasks):
                raise ValueError("Select each native task once")
            for task in tasks:
                public_sources(task)
            root = workspace_root()
            cache = root / "reports/native-image-build.json"
            self.check("before-native-images")
            if build:
                argv = ["sh", str(root / "infra/native/build.sh"), *[task.name for task in tasks]]
                code = run_logged(argv, self.output / "native-build.log", stage="native build", timeout=None)
                if code:
                    raise subprocess.CalledProcessError(code, argv)
                self.check("after-native-build")
                built = {tag: image_id(tag) for tag in image_tags(tasks)}
                write_json(cache, {"sources": self.sources, "images": built})
            recorded = json.loads(cache.read_text())
            if recorded.get("sources") != self.sources:
                raise ValueError("Native image build sources differ; rebuild through the control suite")
            for tag in image_tags(tasks):
                if tag not in recorded.get("images", {}):
                    raise ValueError(f"Native image {tag} is not in the verified build; rebuild without --skip-build")
            self.native_images = {tag: image_id(tag) for tag in image_tags(tasks)}
            if any(recorded.get("images", {}).get(tag) != identity for tag, identity in self.native_images.items()):
                raise ValueError("Native image differs from its verified build")
            self.native_tasks = tasks
            self.report["native_images"] = dict(self.native_images)
            self.report["native_tasks"] = {task.name: str(task) for task in tasks}
            for task in tasks:
                self.pin("native task " + task.name, task)
            write_json(self.output / "native-inputs.json", {task.name: fingerprint(task) for task in tasks})
            self.pin("native inputs", self.output / "native-inputs.json")

    def use_image(self, tag: str) -> str:
        """Pin the content identity behind `tag` under a run-specific tag."""
        self.identity = image_id(tag)
        self.image = pin_base_image(self.identity, self.prefix)
        self.report.update(image_id=self.identity, frozen_image=self.image)
        return self.identity

    def pin(self, name: str, path: Path) -> None:
        """Record bytes that must not change for the rest of the run."""
        self.pinned[name] = (path, fingerprint(path))

    def check(self, phase: str) -> None:
        """Sources, images, selected tasks and every pinned input are as recorded."""
        if source_manifest() != self.sources:
            raise RuntimeError(f"Sources changed ({phase})")
        if self.identity is not None and image_id(self.image or "") != self.identity:
            raise RuntimeError(f"Pinned image changed ({phase})")
        if any(image_id(tag) != identity for tag, identity in self.native_images.items()):
            raise RuntimeError(f"Native image changed ({phase})")
        for name, (path, recorded) in self.pinned.items():
            if fingerprint(path) != recorded:
                raise RuntimeError(f"Pinned {name} changed ({phase})")
        self.report.setdefault("phases", []).append({"phase": phase, "unchanged": True})

    def harbor(
        self,
        job: str,
        tasks: Path,
        agent: str,
        *,
        admission: bool = False,
        **arguments: Any,
    ) -> tuple[int, list[dict]]:
        """Submit declared native packages durably under Harbor phase limits."""
        with self.step(job, log=job):
            if tasks not in self.native_tasks:
                raise ValueError("Only a selected native task can be dispatched")
            if int(arguments.get("attempts") or 1) != 1:
                raise ValueError("Native execution requires one attempt per fresh environment")
            if agent not in UPLOAD_ONLY_AGENTS:
                raise RuntimeError(f"Agent {agent} may run commands beside the shared verifier environment")
            if admission:
                arguments["verifier_env"] = [*arguments.get("verifier_env", []), "SAPI_HOSTED_ADMISSION=1"]
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
            finally:
                trials = load_trials(jobs / job)
                dispatched[job]["trials"] = [
                    {
                        key: row[key]
                        for key in (
                            "task_name",
                            "trial_id",
                            "trial_path",
                            "evidence_path",
                            "partial",
                        )
                        if key in row
                    }
                    for row in trials
                ]
            return code, trials

    def transport(self, task: Path, *, skip_build: bool = False) -> int:
        """Run the trusted transport control with native Harbor phase limits."""
        with self.step("transport", log="transport"):
            self.pin("transport-task", task)
            self.check("before transport")
            jobs = self.output / "jobs"
            if (jobs / "transport").exists():
                raise ValueError("A transport job cannot be dispatched twice")
            argv = job_args(self.harbor_argv, task, jobs, "transport", "nop")
            # Docker still removes containers/networks; retained engine bytes serve later adapters and skip-build.
            argv.append("--no-delete")
            if skip_build:
                argv.remove("--force-build")
            self.report.setdefault("commands", []).append(argv)
            reference: dict[str, Any] = {"path": "jobs/transport", "status": "dispatched"}
            self.report.setdefault("harbor_jobs", {})["transport"] = reference
            code = run_logged(argv, self.output / "transport.log", stage="transport", timeout=None)
            reference.update(status="finished", exit_code=code)
            self.check("after transport")
            return code

    @contextmanager
    def bridge(self, budget: dict, name: str, *, bindings: Path, reject_tool_use=False) -> Iterator[Path]:
        """An Agency bridge enforcing `budget`; yields its audit path and always stops it."""
        budget_path = self.output / "budgets" / (name + ".json")
        audit = self.output / "audits" / (name + ".jsonl")
        budget_path.parent.mkdir(exist_ok=True)
        write_json(budget_path, budget)
        self.pin("budget " + name, budget_path)
        self.report.setdefault("logs", {})[name + "-bridge"] = name + "-bridge.log"
        self.bridge_process = start_bridge(
            self.host.listen_host,
            self.host.bridge_port,
            self.host.wrapper_url,
            audit,
            budget_path,
            self.output / (name + "-bridge.log"),
            bindings=bindings,
            reject_tool_use=reject_tool_use,
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
                if self.report.get("status") != "interrupted":
                    self.report["status"] = "failed"

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
        cleanup("containers", containers)
        self.report["finished_at"] = datetime.now(UTC).isoformat()
        self.report.setdefault("logs", {}).update({path.stem: path.name for path in self.output.glob("*.log")})
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
    report.setdefault("logs", {})
    report["started_at"] = datetime.now(UTC).isoformat()
    sources = source_manifest()
    write_json(output / "source-manifest.json", sources)
    report["source_manifest"] = "source-manifest.json"
    run = Run(output, report, sources, prefix, host or HostConfig.from_environment())
    try:
        with run.step("docker preflight"):
            report["docker"] = docker_preflight()
        (output / "existing-containers.txt").write_text(running_containers())
        run.harbor_argv, report["harbor_version"] = checked_harbor()
        body(run)
    except Exception as error:  # A run always ends with a written report
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, subprocess.TimeoutExpired):
            report["failure_category"] = "timeout_unknown_outcome"
        elif classify is not None:
            report["failure_category"] = classify(error)
        stage = report.get("failure_stage", "setup")
        if isinstance(error, subprocess.TimeoutExpired):
            progress(f"outcome unknown at {stage}: timed out after {error.timeout:g}s (not a failure verdict)")
        else:
            cause = f"exit {error.returncode}" if isinstance(error, subprocess.CalledProcessError) else str(error)
            cause = " ".join(cause.splitlines())
            if report.get("failure_category") == "timeout_unknown_outcome":
                progress(f"outcome unknown at {stage}: {cause} (not a failure verdict)")
            else:
                progress(f"failed at {stage}: {cause}")
        if report.get("log"):
            progress(f"  log: {output / report['log']}")
        job = Path(report["log"]).stem if report.get("log") else stage
        if job in report.get("harbor_jobs", {}):
            progress(f"  jobs: {output / report['harbor_jobs'][job]['path']}")
        if report.get("log_tail"):
            progress("  last lines:")
            for line in report["log_tail"][-20:]:
                progress(f"    {line}")
        if report.get("log"):
            progress(f"  inspect: tail -n 200 {output / report['log']}")
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        progress(f"interrupted at {report.get('interrupted_stage', 'setup')}; in-flight work has an unknown outcome")
        raise
    finally:
        progress("finalizing: cleanup, final source check and report")
        run.close()
    return report
