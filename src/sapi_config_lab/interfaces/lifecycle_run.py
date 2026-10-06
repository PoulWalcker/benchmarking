"""Finite blank-slate lifecycle evaluation using Harbor and the existing backend.

Run author on the host; run live inside the isolated lab container with the same
source tree and copied series directory. No native execution is implemented here.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import replace
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
from http.server import HTTPServer
import json
import os
from pathlib import Path
from typing import Any
import secrets
import subprocess
import threading
import time

from sapi_config_lab.core.evidence import sha256
from sapi_config_lab.interfaces.generation.common import summarize_trials
from sapi_config_lab.core.host import harbor_command
from sapi_config_lab.interfaces.harbor import load_trials
from sapi_config_lab.interfaces.live_evidence import load_verifier, reconcile_dispatches
from sapi_config_lab.core.provenance import source_manifest
from sapi_config_lab.interfaces.replay import require
from sapi_config_lab.interfaces.tasks import stage_tasks
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.runtime.agency import make_handler
from sapi_config_lab.runtime.lifecycle import LifecycleController, digest, durable_json
from sapi_config_lab.runtime.n8n.adapter import N8nBackend
from sapi_config_lab.runtime.rebuilder import WrapperRebuilder
from sapi_config_lab.core import profile

POLICY = {
    "authoring": {"authoring": 1, "runtime": 0, "rebuild": 0},
    "authored": {"authoring": 0, "runtime": 2, "rebuild": 0},
    "mutation": {"authoring": 0, "runtime": 4, "rebuild": 2},
}
MODEL = "gpt-6-astra"


def read_json(path):
    return json.loads(Path(path).read_text())


class LifecycleSeries:
    """A process-exclusive, fsynced 1+2+6 series; unknown work cannot resume."""

    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "series.json"
        self.active: str | None = None

    def __enter__(self):
        self.lock = (self.directory / ".series.lock").open("a")
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                self.data = read_json(self.path)
            else:
                self.data = {
                    "schema": "sapi-lab-lifecycle-experiment/v1",
                    "policy": copy.deepcopy(POLICY),
                    "ceilings": {"authoring": 1, "other_wrapper_attempts": 8, "total": 9},
                    "source_manifest": source_manifest(),
                    "phases": [],
                }
                self.save()
            self.validate()
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.lock.close()

    def save(self):
        durable_json(self.path, self.data)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def validate(self):
        require(digest(read_json(self.path)) == digest(self.data), "Series ledger changed during admission")
        require(self.data.get("schema") == "sapi-lab-lifecycle-experiment/v1", "Unknown lifecycle series")
        require(digest(self.data.get("policy")) == digest(POLICY), "Frozen lifecycle policy changed")
        require(
            digest(self.data.get("ceilings")) == digest({"authoring": 1, "other_wrapper_attempts": 8, "total": 9}),
            "Frozen lifecycle ceiling changed",
        )
        require(self.data.get("source_manifest") == source_manifest(), "Frozen sources changed")
        phases = self.data.get("phases")
        require(isinstance(phases, list) and len(phases) <= 3, "Invalid phase ledger")
        for index, row in enumerate(phases):
            require(row["name"] == list(POLICY)[index], "Lifecycle phase order changed")
            require(row["status"] in {"passed", "failed", "unknown"}, "Invalid phase outcome")
            require(index == len(phases) - 1 or row["status"] == "passed", "Continued after nonpassing phase")
            counts = dict.fromkeys(POLICY[row["name"]], 0)
            require(
                len({digest(g["identity"]) for g in row["grants"]}) == len(row["grants"]), "Repeated outgoing identity"
            )
            for number, grant in enumerate(row["grants"]):
                require(grant["number"] == number + 1 and type(grant["number"]) is int, "Grant order changed")
                require(grant["kind"] in counts and grant["status"] in {"passed", "unknown"}, "Invalid outgoing grant")
                require(number == len(row["grants"]) - 1 or grant["status"] == "passed", "Unknown outgoing grant")
                counts[grant["kind"]] += 1
            require(all(counts[k] <= POLICY[row["name"]][k] for k in counts), "Lifecycle phase cap exceeded")
            self.check_grant_order(row)
            if row["status"] == "passed":
                require(all(g["status"] == "passed" for g in row["grants"]), "Passed phase has unknown dispatch")
                self.check_completed_counts(row)
                report_path = self.directory / row["name"] / "report.json"
                require(sha256(report_path) == row["report_sha256"], "Final phase report changed")
                report = read_json(report_path)
                require(
                    report.get("status") == "passed" and report.get("source_unchanged") is True, "Final gate failed"
                )
        if len(phases) > 1 or (phases and phases[0]["status"] == "passed"):
            authored = read_json(self.directory / "authoring/report.json")
            require(
                sha256(self.directory / "submission.yaml") == authored["submission_sha256"], "Original YAML changed"
            )

    def begin(self, name):
        self.validate()
        require(self.active is None, "Phase already running")
        rows = self.data["phases"]
        require(all(r["status"] == "passed" for r in rows), "Prior phase failed or has an unknown outcome")
        require(len(rows) < 3 and name == list(POLICY)[len(rows)], "Phase is repeated or out of order")
        (self.directory / name).mkdir(exist_ok=False)
        rows.append({"name": name, "status": "unknown", "grants": [], "report_sha256": None})
        self.active = name
        self.save()

    @staticmethod
    def check_grant_order(row):
        sequence = [g["identity"]["kind"] if g["kind"] == "runtime" else g["kind"] for g in row["grants"]]
        patterns = {
            "authoring": [["authoring"]],
            "authored": [["Callback", "Cron"]],
            "mutation": [
                ["Callback", "rebuild", "Callback", "Cron"],
                ["Callback", "rebuild", "Callback", "rebuild", "Callback", "Cron"],
            ],
        }
        require(
            any(sequence == pattern[: len(sequence)] for pattern in patterns[row["name"]]),
            "Outgoing grant order changed",
        )

    @staticmethod
    def check_completed_counts(row):
        kinds = [g["kind"] for g in row["grants"]]
        expected = {"authoring": (1, 0, 0), "authored": (0, 2, 0)}
        if row["name"] in expected:
            require(
                tuple(kinds.count(k) for k in ("authoring", "runtime", "rebuild")) == expected[row["name"]],
                "Incomplete phase",
            )
        else:
            require(
                1 <= kinds.count("rebuild") <= 2 and kinds.count("runtime") == kinds.count("rebuild") + 2,
                "Incomplete rebuild/Cron proof",
            )

    def grant(self, kind, identity):
        self.validate()
        require(self.active is not None, "No active phase")
        assert self.active is not None
        row = self.data["phases"][-1]
        grants = row["grants"]
        require(all(g["status"] == "passed" for g in grants), "An outgoing attempt has an unknown outcome")
        require(kind in POLICY[self.active], "Unknown grant kind")
        require(sum(g["kind"] == kind for g in grants) < POLICY[self.active][kind], "Outgoing cap exhausted")
        require(not any(g["identity"] == identity for g in grants), "Repeated outgoing grant")
        if self.active != "authoring":
            expected_kind = "runtime" if not grants or grants[-1]["kind"] == "rebuild" else None
            if kind == "rebuild":
                require(
                    self.active == "mutation"
                    and grants
                    and grants[-1]["kind"] == "runtime"
                    and grants[-1]["identity"]["kind"] == "Callback",
                    "Rebuild must follow candidate Callback",
                )
            else:
                require(identity.get("kind") in {"Callback", "Cron"}, "Invalid event grant")
                if identity["kind"] == "Callback":
                    require(expected_kind == "runtime", "Unexpected candidate test")
                else:
                    require(
                        grants and grants[-1]["kind"] == "runtime" and grants[-1]["identity"]["kind"] == "Callback",
                        "Cron requires the last candidate test",
                    )
                    require(
                        self.active == "authored" or any(g["kind"] == "rebuild" for g in grants),
                        "Mutation Cron requires genuine repair",
                    )
                require(
                    not any(g["kind"] == "runtime" and g["identity"]["kind"] == "Cron" for g in grants),
                    "Cron already completed",
                )
        number = len(grants) + 1
        grants.append({"number": number, "kind": kind, "identity": identity, "status": "unknown"})
        self.save()
        return number

    def complete(self, number):
        self.validate()
        require(self.active is not None, "No active phase")
        grants = self.data["phases"][-1]["grants"]
        require(number == len(grants) and grants[-1]["status"] == "unknown", "Grant cannot complete twice")
        grants[-1]["status"] = "passed"
        self.save()

    def finish(self, report):
        require(self.active is not None, "No active phase")
        assert self.active is not None
        report["source_unchanged"] = self.data["source_manifest"] == source_manifest()
        report["budget"] = {"phase": POLICY[self.active], **self.data["ceilings"]}
        if not report["source_unchanged"]:
            report["status"] = "failed"
        if report["status"] == "passed":
            row = self.data["phases"][-1]
            try:
                require(all(g["status"] == "passed" for g in row["grants"]), "Unconfirmed dispatch")
                self.check_completed_counts(row)
            except ValueError as error:
                report.update(status="failed", error=str(error))
        path = self.directory / self.active / "report.json"
        durable_json(path, report)
        row = self.data["phases"][-1]
        row["status"] = report["status"]
        row["report_sha256"] = sha256(path)
        self.save()
        self.active = None


def native_controls(directory, image, frozen):
    """Fresh oracle+nop prove this image and copied verifier before paid authoring."""
    image_id = subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()
    pinned_image = "sapi-config-lab-lifecycle-base:" + image_id.split(":")[-1][:16]
    subprocess.check_call(["docker", "tag", image_id, pinned_image])
    directory.mkdir()
    stage_tasks(directory / "tasks", image=pinned_image, scenarios=("daily-digest",))
    report: dict[str, Any] = {"status": "failed", "image_id": image_id, "image": pinned_image, "checks": []}
    try:
        for agent in ("oracle", "nop"):
            args = [
                *harbor_command(),
                "run",
                "--path",
                str(directory / "tasks"),
                "--agent",
                agent,
                "--n-attempts",
                "1",
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--jobs-dir",
                str(directory / "jobs"),
                "--job-name",
                agent,
                "--force-build",
            ]
            with (directory / (agent + ".log")).open("w") as log:
                done = subprocess.run(args, cwd=workspace_root(), stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            trials = load_trials(directory / "jobs" / agent)
            passed = (
                done.returncode == 0
                and len(trials) == 1
                and not trials[0]["exception"]
                and trials[0]["rewards"] == {"reward": 1.0 if agent == "oracle" else 0.0}
            )
            if agent == "oracle" and passed:
                acceptance = trials[0]["acceptance"]
                passed = acceptance.get("passed") is True and acceptance.get("runtime_source_manifest") == {
                    path: value for path, value in frozen.items() if path.startswith("src/")
                }
            report["checks"].append({"agent": agent, "passed": passed, "trials": trials, "command": args})
            require(passed, "Native lifecycle " + agent + " control failed; no authoring dispatched")
        require(source_manifest() == frozen, "Sources changed during lifecycle controls")
        report["status"] = "passed"
    finally:
        durable_json(directory / "report.json", report)
    return report


def author(series, *, upstream, image):
    series.begin("authoring")
    directory = series.directory / "authoring"
    report: dict[str, Any] = {"status": "failed", "origin": "generated", "repairs": 0, "model": MODEL}
    try:
        report["controls"] = native_controls(directory / "controls", image, series.data["source_manifest"])
        image = report["controls"]["image"]
        prompts = stage_tasks(directory / "tasks", mode="generation", image=image, scenarios=("daily-digest",))
        report["prompt_sha256"] = prompts["daily-digest"]
        # Evaluation values are created only after author-visible bytes are frozen.
        path = directory / "tasks/daily-digest/tests/cases.json"
        corpus = read_json(path)
        marker = secrets.token_hex(8)
        for case in corpus["daily-digest"]["positive"]:
            for article in case["inputs"]["articles"]:
                article["id"] = marker + "-" + article["id"]
        durable_json(path, corpus)
        report["private_cases_sha256"] = sha256(path)
        args = [
            *harbor_command(),
            "run",
            "--path",
            str(directory / "tasks"),
            "--agent",
            "sapi_config_lab.interfaces.generation.agent:WrapperYamlAgent",
            "--ak",
            "upstream=" + upstream,
            "--n-attempts",
            "1",
            "--n-concurrent",
            "1",
            "--max-retries",
            "0",
            "--jobs-dir",
            str(directory / "jobs"),
            "--job-name",
            "generated",
            "--force-build",
        ]
        report["command"] = args
        grant = series.grant("authoring", {"prompt_sha256": report["prompt_sha256"]})
        with (directory / "harbor.log").open("w") as log:
            completed = subprocess.run(args, cwd=workspace_root(), stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        trials = summarize_trials(directory / "jobs/generated")
        report["trials"] = trials
        require(
            completed.returncode == 0 and len(trials) == 1 and trials[0]["passed"], "Fresh authoring/stub gate failed"
        )
        raw, generation = bind_authoring_evidence(
            trials[0], report["prompt_sha256"], report["private_cases_sha256"], series.data["source_manifest"]
        )
        require(
            generation.get("model") == MODEL and generation.get("generation_calls") == 1, "Wrong authoring model/count"
        )
        require(
            not generation.get("observed_tool_markers") and generation.get("repairs") == 0,
            "Authoring used tools or repairs",
        )
        report["submission_sha256"] = hashlib.sha256(raw).hexdigest()
        require(report["submission_sha256"] == generation["submission_sha256"], "Original generation bytes changed")
        (series.directory / "submission.yaml").write_bytes(raw)
        series.complete(grant)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        series.finish(report)
    return report


def bind_authoring_evidence(trial, prompt_hash, fixture_hash, frozen):
    """Bind one original response to the exact prompt and independently tested bytes."""
    directory = Path(trial["result_path"]).parent
    raw = (directory / "agent/submission.yaml").read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    generation = read_json(directory / "agent/generation.json")
    acceptance = read_json(directory / "verifier/report.json")
    require(generation == trial["generation"] and acceptance == trial["acceptance"], "Trial evidence changed")
    require(generation.get("submission_sha256") == actual_hash, "Generated response hash changed")
    require(
        generation.get("prompt_sha256") == prompt_hash and sha256(directory / "agent/prompt.txt") == prompt_hash,
        "Authoring used another prompt",
    )
    require(
        acceptance.get("submission_sha256") == actual_hash
        and (directory / "verifier/submission.yaml").read_bytes() == raw,
        "Verifier accepted different YAML bytes",
    )
    require(acceptance.get("fixture_sha256") == fixture_hash, "Verifier used another fixture corpus")
    require(
        acceptance.get("runtime_source_manifest") == {p: h for p, h in frozen.items() if p.startswith("src/")},
        "Verifier executed another runtime source",
    )
    require(acceptance.get("passed") is True, "Independent stub acceptance failed")
    return raw, generation


class AdmittedNativeBackend:
    """A bounded transport decorator; compilation/execution delegate to N8nBackend."""

    name = "n8n"

    def __init__(self, series, directory, upstream):
        self.series, self.directory, self.upstream = series, directory, upstream
        self.backend = N8nBackend()
        self.pending = None
        self.correlations = []

    def compile(self, config, bindings, options):
        require(
            sorted(s["uses"] for s in config["workflow"]["steps"])
            == ["digest.prepare", "digest.preview", "digest.summarize"],
            "Candidate operation set changed",
        )
        steps = [s for s in config["workflow"]["steps"] if s["kind"] == "LLM"]
        require(len(steps) == 1 and steps[0]["uses"] == "digest.summarize", "Candidate model-call shape changed")
        require(options.admission is not None and options.deadline_at is not None, "Missing lifecycle admission")
        number = self.series.grant("runtime", options.admission)
        audit = self.directory / f"runtime-{number}.jsonl"
        workflow = config["workflow"]
        budget = {
            "max_attempts": 1,
            "operations": {"digest.summarize": 1},
            "model": MODEL,
            "occurrences": {f"{workflow['id']}/r{workflow['revision']}/{steps[0]['id']}": "digest.summarize"},
            "expires_at": options.deadline_at,
        }
        server = HTTPServer(("127.0.0.1", 0), make_handler(bindings, self.upstream, 185, audit, budget))
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            compiled = self.backend.compile(config, bindings, replace(options, bridge_url=endpoint))
        except BaseException:
            server.server_close()
            raise
        self.pending = (number, audit, server)
        return compiled

    def execute(self, compiled, artifact_dir):
        require(self.pending is not None, "Native compile admission missing")
        assert self.pending is not None
        number, audit, server = self.pending
        self.pending = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            record = self.backend.execute(compiled, artifact_dir)
            require(record.get("status") == "success", "Native candidate execution failed")
            # Independent native parser and outgoing audit must agree before another grant.
            _, provenance = load_verifier()
            calls = provenance.live_operations(record)
            rows = [json.loads(line) for line in audit.read_text().splitlines()]
            self.correlations.extend(reconcile_dispatches(calls, rows, MODEL))
            self.series.complete(number)
            return record
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def live(series, *, phase, upstream, cron_delay_seconds=300):
    require(phase in {"authored", "mutation"}, "Unknown live phase")
    require(type(cron_delay_seconds) is int and 60 <= cron_delay_seconds <= 900, "Cron delay must be 60..900 seconds")
    series.begin(phase)
    directory = series.directory / phase
    report: dict[str, Any] = {
        "status": "failed",
        "origin": "generated" if phase == "authored" else "intentional_mutation",
    }
    controller = None
    try:
        verifier, _ = load_verifier()
        verify_lifecycle = importlib.import_module(verifier.__package__ + ".lifecycle").verify_lifecycle
        validate_task = importlib.import_module(verifier.__package__ + ".lifecycle_submission").validate_task

        original = profile.read(series.directory / "submission.yaml")
        validate_task(original)
        report["original_submission_sha256"] = sha256(series.directory / "submission.yaml")
        config = copy.deepcopy(original)
        if phase == "mutation":
            config["workflow"]["output"] = {
                "mode": "preview",
                "text": "Intentional broken output",
                "article_ids": ["wrong"],
            }
            durable_json(directory / "intentional-mutation.json", config)
            report["mutation"] = {
                "path": "workflow.output",
                "original": original["workflow"]["output"],
                "replacement": config["workflow"]["output"],
            }
        report["candidate_sha256"] = digest(config)
        backend = AdmittedNativeBackend(series, directory, upstream)
        builder = WrapperRebuilder(upstream, expected_model=MODEL)

        def rebuild(source, findings, target, artifacts):
            number = series.grant("rebuild", {"source_sha256": digest(source), "target": target})
            candidate = builder(source, findings, target, artifacts)
            audit = read_json(artifacts / "dispatch.json")
            require(audit["status"] == "returned" and audit["model"] == MODEL, "Rebuild model completion missing")
            require(audit["candidate_yaml_sha256"] == sha256(artifacts / "candidate.yaml"), "Rebuild bytes changed")
            series.complete(number)
            return candidate

        due = datetime.fromtimestamp(time.time() + cron_delay_seconds, timezone.utc).replace(second=0, microsecond=0)
        overlay = {"schedule": f"{due.minute} {due.hour} * * *", "timezone": "UTC"}
        report["schedule_overlay"] = overlay
        report["due_at"] = due.isoformat()
        controller = LifecycleController(
            directory / "registry",
            backend=backend,
            rebuilder=rebuild if phase == "mutation" else None,
            llm_mode="live",
            bridge_url="http://127.0.0.1:1",
        )
        ref = controller.register(config, schedule_overlay=overlay)
        controller.callback(ref, "initial-test")
        snapshot = controller.snapshot()
        family = snapshot["families"]["daily-digest"]
        require(
            family["active"] is not None and not family["suspended"],
            "Callback/rebuild chain did not release a candidate",
        )
        if phase == "mutation":
            require(1 <= family["rebuild_count"] <= 2, "Mutation did not produce a genuine bounded rebuild")
        else:
            require(not snapshot["rebuilds"] and len(snapshot["definitions"]) == 1, "Authored run changed its YAML")
        require(time.time() < due.timestamp() + 60, "Scheduled minute was missed; no rescheduling or retry")
        while time.time() < due.timestamp():
            time.sleep(min(1, due.timestamp() - time.time()))
        controller.tick()  # Actual wall clock, never an injected test datetime.
        snapshot = controller.snapshot()
        checked = verify_lifecycle(snapshot, mode="live", require_wall_clock=True)
        cron = [e for e in snapshot["events"].values() if e["admission"]["kind"] == "Cron"]
        require(len(cron) == 1 and cron[0]["state"] == "passed", "Missing accepted real Cron invocation")
        require(checked["wall_clock_cron_verified"], "Synthetic clock cannot establish Cron")
        require(len(checked["calls"]) == len(backend.correlations), "Native/audit total differs")
        callbacks = [e for e in snapshot["events"].values() if e["admission"]["kind"] == "Callback"]
        require(
            len(callbacks) == (1 if phase == "authored" else family["rebuild_count"] + 1), "Wrong candidate test count"
        )
        report.update(
            acceptance=checked,
            correlation=backend.correlations,
            model_rebuild_count=len(snapshot["rebuilds"]),
            status="passed",
        )
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        try:
            if controller is not None:
                durable_json(directory / "snapshot.json", controller.snapshot())
        except Exception as error:
            report["status"] = "failed"
            report["collection_error"] = type(error).__name__
        series.finish(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    author_parser = commands.add_parser("author", help="One fresh Harbor authoring, then native controller stub gate")
    author_parser.add_argument("--upstream", required=True)
    author_parser.add_argument("--image", default="sapi-config-lab-n8n:2.41.5")
    live_parser = commands.add_parser(
        "live", help="Run inside the isolated n8n lab container; total series 1+8 calls maximum"
    )
    live_parser.add_argument("--phase", required=True, choices=["authored", "mutation"])
    live_parser.add_argument("--upstream", required=True)
    live_parser.add_argument("--cron-delay-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    with LifecycleSeries(args.series_dir) as series:
        report = (
            author(series, upstream=args.upstream, image=args.image)
            if args.command == "author"
            else live(series, phase=args.phase, upstream=args.upstream, cron_delay_seconds=args.cron_delay_seconds)
        )
    print(json.dumps({"status": report["status"], "error": report.get("error"), "budget": report["budget"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
