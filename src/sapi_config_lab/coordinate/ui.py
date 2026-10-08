"""Import inactive graphs into a local n8n and serve one explicitly bounded manual live run."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
import fcntl
from http.server import HTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request
import uuid
import webbrowser

import yaml

from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.coordinate.wrapper import parse_wrapper_files, wrapper_identity
from sapi_config_lab.evidence import json_text, sha256, write_json
from sapi_config_lab.execute.agency import MAX_OUTGOING_ATTEMPTS, WRAPPER_TIMEOUT_SECONDS, make_handler
from sapi_config_lab.execute.host import HostConfig, local_address
from sapi_config_lab.execute.ui_n8n import DockerUi, fingerprint
from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper
from sapi_config_lab.net import urlopen
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.profile import UniqueLoader, Unsupported, read_bindings, validate, validate_bindings

MAX_GRANT_SECONDS = 3600
DEFAULT_GRANT_SECONDS = 600
PREPARED_FILES = ("config.yaml", "bindings.yaml", "workflow.json", "mapping.json")


def _check_grant_seconds(seconds: int) -> None:
    if type(seconds) is not int or not 1 <= seconds <= MAX_GRANT_SECONDS:
        raise ValueError("A UI grant and deadline must be between one second and one hour")


def prepare(
    config_path: Path, directory: Path, *, host: HostConfig | None = None, deadline_seconds: int | None = None
) -> dict:
    """Compile against the shared catalog without executing; each new directory names one owned workflow."""
    hosted = next((s for s in SCENARIOS.values() if s.hosted and s.config.resolve() == config_path.resolve()), None)
    if hosted is not None:
        raise ValueError(
            f"{hosted.name} is a hosted scenario: its tools and evaluator exist only inside a Harbor trial, "
            "so the UI imports fixture scenarios only"
        )
    host = host or HostConfig.from_environment()
    if not 1024 <= host.ui_bridge_port <= 65535:
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
        _check_grant_seconds(deadline_seconds)
        config["execution"]["deadline_seconds"] = deadline_seconds
    bridge_url = host.container_url(host.ui_bridge_port) + "/v1/agency/execute"
    compiled = default_backend().compile(config, bindings, CompileOptions("live", bridge_url))
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
    if len(occurrences) > MAX_OUTGOING_ATTEMPTS:
        raise ValueError(f"A UI run supports at most {MAX_OUTGOING_ATTEMPTS} outgoing attempts")
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
        "port": host.ui_bridge_port,
        "max_attempts": len(occurrences),
        "operations": dict(Counter(occurrences.values())),
        "occurrences": occurrences,
        "prepared_only": True,
        "hashes": {name: sha256(directory / name) for name in PREPARED_FILES},
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
    names = set(PREPARED_FILES) | ({"original.yaml"} if "overlay" in prepared else set())
    if set(prepared["hashes"]) != names or any(
        sha256(directory / name) != expected for name, expected in prepared["hashes"].items()
    ):
        raise ValueError("Prepared UI artifact changed")
    return prepared


def admit(directory: Path, *, max_attempts: int, seconds: int, model: str) -> dict:
    """Durably grant one prepared workflow; never reopen or expand a used grant."""
    prepared = load_prepared(directory)
    if type(max_attempts) is not int or max_attempts != prepared["max_attempts"]:
        raise ValueError("Explicit attempt cap must match the prepared workflow")
    _check_grant_seconds(seconds)
    budget = {
        "max_attempts": max_attempts,
        "operations": prepared["operations"],
        "occurrences": prepared["occurrences"],
        "model": model,
        "expires_at": time.time() + seconds,
        "workflow_id": prepared["workflow_id"],
    }
    with (directory / "budget.json").open("x") as stream:
        stream.write(json_text(budget, ensure_ascii=True))
        stream.flush()
        os.fsync(stream.fileno())
    return budget


def _wrapper_status(upstream: str) -> int:
    """The wrapper serves POST only, so a GET proves reachability without a prompt or a model call."""
    try:
        with urlopen(Request(upstream, method="GET"), timeout=3) as response:
            return response.status
    except HTTPError as error:
        return error.code


def _outcome(audit: Path) -> dict:
    try:
        rows = [json.loads(line) for line in audit.read_text().splitlines()]
    except (OSError, ValueError) as error:
        return {"status": "unknown", "collection_error": str(error)}
    attempts = sum(r["event"] == "dispatch_attempt" for r in rows)
    completions = sum(r["event"] == "completion" for r in rows)
    failures = sum(r["event"] == "failure" for r in rows)
    return {
        "wrapper_attempts": attempts,
        "wrapper_completions": completions,
        "failures": failures,
        "provider_call_count": None,
        "status": "failed" if failures else "unknown" if attempts != completions else "stopped",
    }


def serve(
    directory: Path,
    *,
    max_attempts: int,
    seconds: int,
    wrapper_evidence: Path,
    wrapper_files: dict[str, Path] | None = None,
    host: HostConfig | None = None,
    on_ready: Callable[[], None] | None = None,
) -> None:
    """Foreground bridge for one prepared workflow; opening or importing the graph never executes it."""
    host = host or HostConfig.from_environment()
    prepared = load_prepared(directory)
    if max_attempts != prepared["max_attempts"]:
        raise ValueError("Invalid UI cap")
    _check_grant_seconds(seconds)
    if (directory / "budget.json").exists():
        raise ValueError("This UI grant already exists; use 'ui open CONFIG --live' for a fresh copy")
    endpoint = urlparse(host.wrapper_url)
    if endpoint.scheme != "http" or endpoint.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("The UI helper requires a loopback model wrapper")
    identity = wrapper_identity(wrapper_evidence, host.wrapper_url, host.wrapper_model, wrapper_files)
    wrapper_status = _wrapper_status(host.wrapper_url)
    if wrapper_status not in {200, 404, 405, 501}:
        raise ValueError("Model wrapper did not answer the unpaid readiness probe")
    budget = admit(directory, max_attempts=max_attempts, seconds=seconds, model=identity["model"])
    audit = directory / "bridge-audit.jsonl"
    base = make_handler(
        read_bindings(directory / "bindings.yaml"),
        host.wrapper_url,
        WRAPPER_TIMEOUT_SECONDS,
        audit,
        budget,
        transport=request_wrapper,
    )

    class Handler(base):  # type: ignore[valid-type,misc]
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

    server = HTTPServer((host.listen_host, prepared["port"]), Handler)
    ready = {
        "workflow_id": budget["workflow_id"],
        "workflow_url": f"{host.n8n_url}/workflow/{budget['workflow_id']}",
        "health_url": f"http://{local_address(host.listen_host)}:{prepared['port']}/health",
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
        write_json(directory / "stopped.json", _outcome(audit))


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
    host: HostConfig | None = None,
    new_copy: bool = False,
    deadline_seconds: int | None = None,
) -> dict:
    """Compile, register once and return a link; never execute anything."""
    host = host or HostConfig.from_environment()
    with local_state(state), tempfile.TemporaryDirectory(dir=state) as temporary:
        staging = Path(temporary) / "prepared"
        prepared = prepare(config, staging, host=host, deadline_seconds=deadline_seconds)
        document = json.loads((staging / "workflow.json").read_text())
        definition = {key: value for key, value in document.items() if key not in {"id", "name"}}
        key = fingerprint({"source": sha256(config), "bindings": sha256(CATALOG), "graph": definition})
        index_path = state / "index.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        selected = adapter if adapter is not None else DockerUi(host.n8n_container)
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
            "workflow_url": f"{host.n8n_url}/workflow/{prepared['workflow_id']}",
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


def wrapper_preference(
    state: Path, supplied: Path | None, files: dict[str, Path], host: HostConfig
) -> tuple[Path, dict[str, Path]]:
    """The inspected wrapper identity and its local file locations, remembered for later live use."""
    settings = state / "settings.json"
    saved = json.loads(settings.read_text()) if settings.exists() else {}
    path = supplied or (Path(saved["wrapper_evidence"]) if saved.get("wrapper_evidence") else None)
    if path is None:
        raise ValueError("First live use requires --wrapper-evidence PATH to the inspected wrapper identity file")
    files = files or {name: Path(location) for name, location in saved.get("wrapper_files", {}).items()}
    wrapper_identity(path, host.wrapper_url, host.wrapper_model, files)
    state.mkdir(parents=True, exist_ok=True)
    write_json(
        settings,
        {"wrapper_evidence": str(path.resolve()), "wrapper_files": {k: str(v.resolve()) for k, v in files.items()}},
    )
    return path, files


def open_command(parser: argparse.ArgumentParser, args: argparse.Namespace, host: HostConfig) -> None:
    if args.all and args.live:
        parser.error("--all is view-only; select one config for --live")
    if not 1 <= args.seconds <= MAX_GRANT_SECONDS:
        parser.error("--seconds must be between one second and one hour")
    state = args.state_dir or workspace_root() / "var/ui"
    # Hosted scenarios need their host environment; the UI imports fixture workflows only.
    configs = [s.config for s in SCENARIOS.values() if not s.hosted] if args.all else [args.config]
    wrapper = None
    if args.live:
        # Validate and compile before contacting Docker or the wrapper; script-only graphs need no bridge.
        with tempfile.TemporaryDirectory() as temporary:
            preview = prepare(args.config, Path(temporary) / "prepared", host=host, deadline_seconds=args.seconds)
        if preview["max_attempts"]:
            wrapper = wrapper_preference(state, args.wrapper_evidence, parse_wrapper_files(args.wrapper_file), host)
    rows = []
    for config in configs:
        try:
            row = open_workflow(
                config,
                state,
                host=host,
                new_copy=args.new_copy or args.live,
                deadline_seconds=args.seconds if args.live else None,
            )
        except Unsupported as error:
            if not args.all:
                raise ValueError(f"This config needs the lifecycle controller (sapi-lab lifecycle): {error}") from error
            row = {"config": str(config), "status": "controller_required", "reason": str(error), "executed": False}
        rows.append(row)
        print(json.dumps(row), flush=True)
        if not args.no_browser and not args.live and not args.all and row.get("workflow_url"):
            webbrowser.open(row["workflow_url"])
    if args.all and not args.no_browser:
        webbrowser.open(host.n8n_url)
    if not args.live:
        return
    row = rows[0]

    def ready():
        print("Fresh copy ready. Click Execute workflow in n8n; no execution has started.", flush=True)
        if not args.no_browser:
            webbrowser.open(row["workflow_url"])

    if not row["max_attempts"]:
        ready()
        return
    assert wrapper is not None
    serve(
        Path(row["directory"]),
        max_attempts=row["max_attempts"],
        seconds=args.seconds,
        wrapper_evidence=wrapper[0],
        wrapper_files=wrapper[1],
        host=host,
        on_ready=ready,
    )


def main(argv=None) -> int:
    host = HostConfig.from_environment()
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
    view.add_argument("--container", default=host.n8n_container, help="Local n8n container (SAPI_N8N_CONTAINER)")
    view.add_argument("--port", type=int, default=host.ui_bridge_port, help="UI bridge port (SAPI_UI_BRIDGE_PORT)")
    view.add_argument("--seconds", type=int, default=DEFAULT_GRANT_SECONDS, help="Live deadline and grant lifetime")
    view.add_argument("--wrapper-evidence", type=Path, help="Inspected wrapper identity; saved for subsequent live use")
    view.add_argument("--wrapper-file", action="append", help="NAME=PATH of an inspected wrapper file on this machine")
    view.add_argument("--host", default=host.listen_host, help="UI bridge bind address (SAPI_LISTEN_HOST)")
    prep = commands.add_parser("prepare", help="Compile an inactive UI graph; never call a model")
    prep.add_argument("config", type=Path)
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.add_argument("--port", type=int, default=host.ui_bridge_port)
    run = commands.add_parser(
        "serve", help="Start a foreground bounded bridge; manual workflow execution remains explicit"
    )
    run.add_argument("directory", type=Path)
    run.add_argument("--max-attempts", type=int, required=True)
    run.add_argument("--seconds", type=int, default=DEFAULT_GRANT_SECONDS)
    run.add_argument("--wrapper-evidence", type=Path, required=True)
    run.add_argument("--wrapper-file", action="append", help="NAME=PATH of an inspected wrapper file on this machine")
    run.add_argument("--host", default=host.listen_host, help="UI bridge bind address (SAPI_LISTEN_HOST)")
    args = parser.parse_args(argv)
    overrides = {"port": "ui_bridge_port", "container": "n8n_container", "host": "listen_host"}
    host = replace(
        host, **{field: getattr(args, option) for option, field in overrides.items() if hasattr(args, option)}
    )
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.config, args.output_dir, host=host)))
        elif args.command == "serve":
            serve(
                args.directory,
                max_attempts=args.max_attempts,
                seconds=args.seconds,
                wrapper_evidence=args.wrapper_evidence,
                wrapper_files=parse_wrapper_files(args.wrapper_file),
                host=host,
            )
        else:
            open_command(parser, args, host)
    except (ValueError, OSError, yaml.YAMLError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
