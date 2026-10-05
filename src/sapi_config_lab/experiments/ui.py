"""Prepare an inactive native UI graph and serve one explicitly bounded live run."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from http.server import HTTPServer
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request
from urllib.parse import urlparse
from pathlib import Path
import uuid
import webbrowser

import yaml

from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.experiments.ui_n8n import DockerUi, fingerprint
from sapi_config_lab.runtime.composition import default_backend
from sapi_config_lab.runtime.contracts import CompileOptions
from sapi_config_lab.workflow.profile import UniqueLoader, Unsupported, read_bindings, validate, validate_bindings


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def prepare(config_path: Path, directory: Path, *, port: int = 18766, deadline_seconds: int | None = None) -> dict:
    """Compile without execution; each new directory names one owned workflow."""
    if not 1024 <= port <= 65535:
        raise ValueError("Use a non-privileged valid bridge port")
    if directory.exists():
        raise FileExistsError("Choose a new UI run directory")
    raw = config_path.read_bytes()
    config = yaml.load(raw, Loader=UniqueLoader)
    bindings_raw = CATALOG.read_bytes()
    bindings = validate_bindings(yaml.load(bindings_raw, Loader=UniqueLoader)["operations"])
    validate(config, bindings)
    original_deadline = config["execution"]["deadline_seconds"]
    if deadline_seconds is not None:
        if type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 3600:
            raise ValueError("UI deadline must be between one second and one hour")
        config["execution"]["deadline_seconds"] = deadline_seconds
    compiled = default_backend().compile(
        config, bindings, CompileOptions("live", f"http://host.docker.internal:{port}/v1/agency/execute")
    )
    workflow = config["workflow"]
    refinement = config["execution"].get("refinement")
    repeats = refinement["max_attempts"] if refinement else 1
    occurrences = {}
    for step in workflow["steps"]:
        if step["kind"] != "LLM":
            continue
        for number in range(1, repeats + 1):
            key = f"{workflow['id']}/r{workflow['revision']}/{step['id']}"
            if refinement:
                key += f"/attempt{number}"
            occurrences[key] = step["uses"]
    if len(occurrences) > 8:
        raise ValueError("A UI run supports at most eight outgoing attempts")
    native_id = uuid.uuid4().hex[:16]
    document = dict(compiled.document)
    document.update(id=native_id, name=f"Sapi lab / {workflow['id']} / {native_id}", active=False)
    directory.mkdir(parents=True)
    (directory / "config.yaml").write_bytes(
        raw if deadline_seconds is None else yaml.safe_dump(config, sort_keys=False).encode()
    )
    (directory / "bindings.yaml").write_bytes(bindings_raw)
    write_json(directory / "workflow.json", document)
    write_json(directory / "mapping.json", compiled.mapping)
    prepared = {
        "schema": "sapi-lab-ui-run/v1",
        "workflow_id": native_id,
        "logical_workflow": workflow["id"],
        "source_origin": "supplied_yaml",
        "port": port,
        "max_attempts": len(occurrences),
        "operations": dict(Counter(occurrences.values())),
        "occurrences": occurrences,
        "prepared_only": True,
        "hashes": {
            name: sha256(directory / name) for name in ("config.yaml", "bindings.yaml", "workflow.json", "mapping.json")
        },
    }
    if deadline_seconds is not None:
        (directory / "original.yaml").write_bytes(raw)
        prepared["hashes"]["original.yaml"] = sha256(directory / "original.yaml")
        prepared["source_origin"] = "supplied_yaml_with_deadline_overlay"
        prepared["overlay"] = {
            "execution.deadline_seconds": {"original": original_deadline, "prepared": deadline_seconds}
        }
    write_json(directory / "prepared.json", prepared)
    return prepared


def load_prepared(directory: Path) -> dict:
    path = directory / "prepared.json"
    if not path.is_file():
        raise ValueError(
            f"No prepared workflow in {directory}. Use 'sapi-lab ui open CONFIG --live' "
            "to prepare, import and start a fresh manual-run grant."
        )
    prepared = json.loads(path.read_text())
    if prepared.get("schema") != "sapi-lab-ui-run/v1":
        raise ValueError("Unknown UI preparation")
    names = {"config.yaml", "bindings.yaml", "workflow.json", "mapping.json"}
    if "overlay" in prepared:
        names.add("original.yaml")
    if set(prepared["hashes"]) != names or any(
        sha256(directory / name) != expected for name, expected in prepared["hashes"].items()
    ):
        raise ValueError("Prepared UI artifact changed")
    return prepared


def admit(directory: Path, *, max_attempts: int, seconds: int, model: str = "gpt-6-astra") -> dict:
    """Durably grant one prepared workflow; never reopen or expand a used grant."""
    prepared = load_prepared(directory)
    if type(max_attempts) is not int or max_attempts != prepared["max_attempts"]:
        raise ValueError("Explicit attempt cap must match the prepared workflow")
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError("UI grant must expire within one hour")
    budget = {
        "max_attempts": max_attempts,
        "operations": prepared["operations"],
        "occurrences": prepared["occurrences"],
        "model": model,
        "expires_at": time.time() + seconds,
        "workflow_id": prepared["workflow_id"],
    }
    with (directory / "budget.json").open("x") as stream:
        stream.write(json.dumps(budget, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return budget


def serve(
    directory: Path,
    *,
    max_attempts: int,
    seconds: int,
    wrapper_evidence: Path,
    host: str = "127.0.0.1",
    upstream: str = "http://127.0.0.1:8765/run",
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Foreground server; opening/importing the graph does not execute it."""
    prepared = load_prepared(directory)
    if max_attempts != prepared["max_attempts"] or not 1 <= seconds <= 3600:
        raise ValueError("Invalid UI cap or grant duration")
    if (directory / "budget.json").exists():
        raise ValueError("This UI grant already exists; use 'ui open CONFIG --live' for a fresh copy")
    from sapi_config_lab.experiments.live import wrapper_identity
    from sapi_config_lab.runtime.agency import make_handler, urlopen

    endpoint = urlparse(upstream)
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("The UI helper requires the existing loopback wrapper")
    identity = wrapper_identity(wrapper_evidence, upstream)
    # The existing wrapper implements POST only. A GET response proves HTTP
    # reachability without submitting a prompt or creating a model call.
    try:
        with urlopen(Request(upstream, method="GET"), timeout=3) as response:
            wrapper_status = response.status
    except HTTPError as error:
        wrapper_status = error.code
    if wrapper_status not in {200, 404, 405, 501}:
        raise ValueError("Existing wrapper did not answer the unpaid readiness probe")
    budget = admit(directory, max_attempts=max_attempts, seconds=seconds, model=identity["model"])
    audit = directory / "bridge-audit.jsonl"
    base = make_handler(read_bindings(directory / "bindings.yaml"), upstream, 185, audit, budget)

    class Handler(base):  # type: ignore[valid-type,misc]  # Existing HTTP handler factory.
        def do_GET(self):
            if self.path != "/health":
                self.reply(404, {"error": "not_found"})
                return
            records = [json.loads(line) for line in audit.read_text().splitlines()]
            spent = sum(r["event"] == "dispatch_attempt" for r in records)
            failed = any(r["event"] == "failure" for r in records)
            self.reply(
                200,
                {
                    "service": "sapi-lab-ui-agency",
                    "workflow_id": budget["workflow_id"],
                    "ready": not failed and time.time() < budget["expires_at"] and spent < max_attempts,
                    "remaining_attempts": max_attempts - spent,
                    "expires_at": budget["expires_at"],
                    "wrapper_http_status": wrapper_status,
                    "readiness_model_calls": 0,
                },
            )

    server = HTTPServer((host, prepared["port"]), Handler)
    ready = {
        "workflow_id": budget["workflow_id"],
        "workflow_url": "http://localhost:5678/workflow/" + budget["workflow_id"],
        "health_url": f"http://127.0.0.1:{prepared['port']}/health",
        "max_attempts": max_attempts,
        "expires_at": budget["expires_at"],
        "wrapper_http_status": wrapper_status,
        "model_calls": 0,
        "instruction": "Open the inactive workflow, then explicitly click Execute workflow. No execution has started.",
    }
    write_json(directory / "ready.json", ready)
    print(json.dumps(ready), flush=True)
    timer = threading.Timer(seconds, server.shutdown)
    timer.daemon = True
    timer.start()
    try:
        if on_ready is not None:
            on_ready()
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
        try:
            rows = [json.loads(line) for line in audit.read_text().splitlines()]
            attempts = sum(r["event"] == "dispatch_attempt" for r in rows)
            completions = sum(r["event"] == "completion" for r in rows)
            failures = sum(r["event"] == "failure" for r in rows)
            outcome = {
                "wrapper_attempts": attempts,
                "wrapper_completions": completions,
                "failures": failures,
                "provider_call_count": None,
                "status": "failed" if failures else "unknown" if attempts != completions else "stopped",
            }
        except Exception as error:
            outcome = {"status": "unknown", "collection_error": str(error)}
        write_json(directory / "stopped.json", outcome)


@contextmanager
def local_state(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another UI registration is in progress; wait for it to finish") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def open_workflow(
    config: Path,
    state: Path,
    *,
    adapter=None,
    container: str = "n8n-n8n-1",
    port: int = 18766,
    new_copy: bool = False,
    deadline_seconds: int | None = None,
) -> dict:
    """Compile, safely register once, and return a link; never execute anything."""
    with local_state(state), tempfile.TemporaryDirectory(dir=state) as temporary:
        staging = Path(temporary) / "prepared"
        prepared = prepare(config, staging, port=port, deadline_seconds=deadline_seconds)
        document = json.loads((staging / "workflow.json").read_text())
        definition = {key: value for key, value in document.items() if key not in {"id", "name"}}
        key = fingerprint({"source": sha256(config), "bindings": sha256(CATALOG), "graph": definition})
        index_path = state / "index.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        selected = adapter if adapter is not None else DockerUi(container)
        ownership_key = selected.identity + ":" + key
        existing = selected.workflows()
        entry = index.get(ownership_key)
        reused = entry is not None and not new_copy
        edited = False
        active = False
        if reused:
            assert entry is not None
            if entry.get("status") != "imported":
                raise ValueError("Previous import outcome is unknown; inspect its artifacts before using --new-copy")
            saved = existing.get(entry["workflow_id"])
            if saved is None:
                raise ValueError("Owned workflow was removed; use --new-copy for a new graph")
            edited = fingerprint(saved) != entry["export_sha256"]
            active = saved.get("active", False)
            directory = Path(entry["directory"])
            prepared = load_prepared(directory)
        else:
            directory = state.resolve() / "copies" / prepared["workflow_id"]
            directory.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), directory)
            entry = {"status": "pending", "workflow_id": prepared["workflow_id"], "directory": str(directory)}
            index[ownership_key] = entry
            write_json(index_path, index)
            imported = selected.import_new(directory / "workflow.json", existing=existing)
            write_json(directory / "imported.json", imported)
            entry.update(status="imported", export_sha256=fingerprint(imported))
            write_json(index_path, index)
        return {
            "config": str(config),
            "status": "reused_edited" if edited else "reused" if reused else "imported",
            "workflow_id": prepared["workflow_id"],
            "workflow_url": "http://localhost:5678/workflow/" + prepared["workflow_id"],
            "directory": str(directory),
            "active": active,
            "matches_prepared": not edited,
            "notice": (
                "Edited in n8n; showing the existing copy. Use --new-copy for a fresh YAML copy." if edited else None
            ),
            "executed": False,
            "model_calls": 0,
            "max_attempts": prepared["max_attempts"],
            "overlay": prepared.get("overlay"),
        }


def wrapper_preference(state: Path, supplied: Path | None) -> Path:
    from sapi_config_lab.experiments.live import wrapper_identity

    settings = state / "settings.json"
    saved = json.loads(settings.read_text()) if settings.exists() else {}
    path = supplied or (Path(saved["wrapper_evidence"]) if saved.get("wrapper_evidence") else None)
    if path is None:
        raise ValueError("First live use requires --wrapper-evidence PATH to the inspected wrapper identity file")
    wrapper_identity(path, "http://127.0.0.1:8765/run")
    state.mkdir(parents=True, exist_ok=True)
    write_json(settings, {"wrapper_evidence": str(path.resolve())})
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("open", help="Compile and import inactive graphs, then open their editor links")
    selection = view.add_mutually_exclusive_group(required=True)
    selection.add_argument("config", nargs="?", type=Path)
    selection.add_argument("--all", action="store_true")
    view.add_argument("--live", action="store_true", help="Arm one fresh copy for manual execution; never auto-run")
    view.add_argument("--new-copy", action="store_true", help="Preserve a changed owned graph and import a new copy")
    view.add_argument("--no-browser", action="store_true")
    view.add_argument("--state-dir", type=Path)
    view.add_argument("--container", default="n8n-n8n-1")
    view.add_argument("--port", type=int, default=18766)
    view.add_argument("--seconds", type=int, default=600, help="Live workflow deadline and foreground grant lifetime")
    view.add_argument("--wrapper-evidence", type=Path, help="Inspected wrapper identity; saved for subsequent live use")
    view.add_argument("--host", default="127.0.0.1")
    prep = commands.add_parser("prepare", help="Compile an inactive UI graph; never call a model")
    prep.add_argument("config", type=Path)
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.add_argument("--port", type=int, default=18766)
    run = commands.add_parser(
        "serve", help="Start a foreground bounded bridge; manual workflow execution remains explicit"
    )
    run.add_argument("directory", type=Path)
    run.add_argument("--max-attempts", type=int, required=True)
    run.add_argument("--seconds", type=int, default=600)
    run.add_argument("--wrapper-evidence", type=Path, required=True)
    run.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.config, args.output_dir, port=args.port)))
        elif args.command == "serve":
            serve(
                args.directory,
                max_attempts=args.max_attempts,
                seconds=args.seconds,
                wrapper_evidence=args.wrapper_evidence,
                host=args.host,
            )
        else:
            if args.all and args.live:
                parser.error("--all is view-only; select one config for --live")
            if not 1 <= args.seconds <= 3600:
                parser.error("--seconds must be between one second and one hour")
            state = args.state_dir or workspace_root() / "var/ui"
            configs = sorted((workspace_root() / "configs").glob("*.yaml")) if args.all else [args.config]
            evidence = None
            if args.live:
                # Finish local compile/validation before contacting Docker or
                # looking up live admission. Script-only graphs need no bridge.
                with tempfile.TemporaryDirectory() as temporary:
                    preview = prepare(
                        args.config, Path(temporary) / "prepared", port=args.port, deadline_seconds=args.seconds
                    )
                if preview["max_attempts"]:
                    evidence = wrapper_preference(state, args.wrapper_evidence)
            rows = []
            for config in configs:
                try:
                    row = open_workflow(
                        config,
                        state,
                        container=args.container,
                        port=args.port,
                        new_copy=args.new_copy or args.live,
                        deadline_seconds=args.seconds if args.live else None,
                    )
                except Unsupported as error:
                    row = {
                        "config": str(config),
                        "status": "controller_required",
                        "reason": str(error),
                        "guide": str(workspace_root() / "docs/LIFECYCLE.md"),
                        "executed": False,
                    }
                    if not args.all:
                        raise ValueError(
                            "This config requires the lifecycle controller. See docs/LIFECYCLE.md"
                        ) from error
                rows.append(row)
                print(json.dumps(row), flush=True)
                if not args.no_browser and not args.live and not args.all and row.get("workflow_url"):
                    webbrowser.open(row["workflow_url"])
            if args.all and not args.no_browser:
                webbrowser.open("http://localhost:5678")
            if args.live:
                row = rows[0]

                def ready():
                    print("Fresh copy ready. Click Execute workflow in n8n; no execution has started.", flush=True)
                    if not args.no_browser:
                        webbrowser.open(row["workflow_url"])

                if row["max_attempts"]:
                    assert evidence is not None
                    serve(
                        Path(row["directory"]),
                        max_attempts=row["max_attempts"],
                        seconds=args.seconds,
                        wrapper_evidence=evidence,
                        host=args.host,
                        on_ready=ready,
                    )
                else:
                    ready()
    except (ValueError, OSError, yaml.YAMLError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
