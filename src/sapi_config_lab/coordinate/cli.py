"""The sapi-lab command line. Optional experiment dependencies are imported only by the command that needs them."""

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys

from sapi_config_lab.contracts import CompileOptions, WorkflowBackend
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.evidence import write_json
from sapi_config_lab.execute.host import LAB_IMAGE
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.profile import Invalid, Unsupported, read, read_bindings, validate

MODULES = {
    "harbor": "coordinate.controls",
    "evaluate": "coordinate.evaluation",
    "live": "coordinate.live",
    "select": "coordinate.replay",
    "generate": "coordinate.generate",
    "bridge": "execute.agency",
    "transport": "coordinate.transport",
    "ui": "coordinate.ui",
    "review-export": "evaluate.review_export",
    "lifecycle": "coordinate.lifecycle",
    "hosted-worker": "coordinate.hosted_worker",
}

PUBLIC = {
    "benchmarks": "List benchmark metadata without loading evaluators.",
    "check": "Run every required local check (tests, lint, format, types, distribution); no Docker.",
    "compile": "Validate one YAML; write the n8n JSON and its step-to-node map.",
    "build": "Compile every benchmarks/*/config.yaml and record which were rejected.",
    "harbor": "Unpaid control suite: pinned image, transport probes, oracle and nop trials.",
    "generate": "Model-authored YAML after the control suite passes. Costs model calls.",
    "select": "Select generated submissions for replay by a fixed rule; runs nothing.",
    "live": "Replay saved or reference submissions against live operations. Costs model calls.",
    "evaluate": "Evaluate one recorded trial again; a judge is called only with --dispatch-judge.",
    "ui": "Import graphs into local n8n and prepare one bounded manual session.",
    "lifecycle": "The durable lifecycle controller against a registry directory.",
    "review-export": "Write a derived analysis.md beside recorded evaluations; adds files only.",
    "fetch-source": "Fetch a pinned upstream source into .cache/ and verify every byte against provenance/.",
}

# Run by a container or another command; each needs an environment a host checkout lacks.
INTERNAL = {
    "execute": "Run one config through the engine. Needs the real n8n CLI on PATH.",
    "package-tasks": "Assemble Harbor task packages into a new directory; runs nothing.",
    "transport": "HTTP transport probes. Runs inside the lab image; run.sh invokes it there.",
    "bridge": "Foreground Agency HTTP adapter. live and ui start it themselves.",
    "hosted-worker": "Trusted verifier step inside a hosted task container; needs /tests and /logs.",
}


def command_help() -> str:
    """Render the two tiers for the epilog; argparse must not reflow this."""
    rows = ["commands you run:"]
    rows += [f"  {name:<21}{text}" for name, text in PUBLIC.items()]
    rows += ["", "internal commands (a container or another command runs these, not you):"]
    rows += [f"  {name:<21}{text}" for name, text in INTERNAL.items()]
    rows += ["", "Every command accepts --help."]
    return "\n".join(rows)


# The required local checks, in order; each is the plain command docs/DEVELOPMENT.md used to list.
LINTED = ("src", "tests", "verification", "infra")
CHECKS = {
    "unittest": ("-m", "unittest", "discover", "-s", "tests", "-v"),
    "ruff check": ("-m", "ruff", "check", *LINTED),
    "ruff format": ("-m", "ruff", "format", "--check", *LINTED),
    "mypy": ("-m", "mypy"),
    "distribution": ("infra/check_distribution.py",),
}


def check_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="sapi-lab check", description="Run every required local check.")
    parser.parse_args(argv)
    failed = []
    for index, (name, arguments) in enumerate(CHECKS.items(), 1):
        print(f"[{index}/{len(CHECKS)}] {name}", file=sys.stderr, flush=True)
        if subprocess.run([sys.executable, *arguments], cwd=workspace_root(), check=False).returncode:
            failed.append(name)
            print(f"[{index}/{len(CHECKS)}] {name}: FAILED", file=sys.stderr, flush=True)
    print("check " + ("failed: " + ", ".join(failed) if failed else "passed"), file=sys.stderr, flush=True)
    return 1 if failed else 0


def benchmarks_command(argv: list[str]) -> int:
    from sapi_config_lab.coordinate.benchmark_discovery import list_benchmarks

    parser = argparse.ArgumentParser(description="List benchmark metadata without loading trusted code.")
    parser.add_argument(
        "--root", type=Path, help="explicit benchmark search root; defaults to this workspace's benchmarks"
    )
    parser.add_argument("--defaults", action="store_true", help="list only default-selected benchmarks")
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else workspace_root() / "benchmarks"
    rows = [
        {"id": item.name, "default": item.default, "version": item.version, "directory": str(item.directory)}
        for item in list_benchmarks(root)
        if not args.defaults or item.default
    ]
    print(json.dumps(rows, indent=2))
    return 0


def compile_command(argv: list[str], *, backend: WorkflowBackend | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate YAML and produce n8n JSON; does not execute it.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, default=CATALOG)
    parser.add_argument("--llm-mode", choices=["stub", "live"], default="stub")
    parser.add_argument("--bridge-url")
    parser.add_argument("--request-timeout-seconds", type=int, default=190)
    args = parser.parse_args(argv)
    selected = backend if backend is not None else default_backend()
    compiled = selected.compile(
        read(args.config),
        read_bindings(args.bindings),
        CompileOptions(args.llm_mode, args.bridge_url, args.request_timeout_seconds),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, compiled.document)
    write_json(args.output.with_suffix(".map.json"), compiled.mapping, ensure_ascii=True)
    print(json.dumps({"compiled": True, "executed": False, "artifact": str(args.output), "llm_mode": args.llm_mode}))
    return 0


def build_command(argv: list[str], *, backend: WorkflowBackend | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile supported examples; reject unsupported extensions.")
    parser.add_argument("--output-dir", type=Path, default=Path("generated"))
    args = parser.parse_args(argv)
    from sapi_config_lab.coordinate.scenarios import SCENARIOS

    rows = []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = backend if backend is not None else default_backend()
    for scenario in SCENARIOS.values():
        path, bindings = scenario.config, read_bindings(scenario.bindings)
        # A hosted scenario's tools are served at run time; any URL compiles.
        options = CompileOptions(operation_url="http://tools/tools" if scenario.hosted else None)
        cfg = read(path)
        order, _ = validate(cfg, bindings)
        row = {
            "config": path.parent.name,
            "valid_profile": True,
            "steps": len(order),
            "runtime_tested": False,
            "engine": selected.name,
        }
        try:
            compiled = selected.compile(cfg, bindings, options)
            out = args.output_dir / (path.parent.name + "." + selected.name + ".json")
            write_json(out, compiled.document)
            write_json(out.with_suffix(".map.json"), compiled.mapping, ensure_ascii=True)
            row.update(demo_export="generated", artifact=out.name)
        except Unsupported as error:
            row.update(demo_export="rejected", reason=str(error))
        rows.append(row)
    write_json(args.output_dir / "build-results.json", rows, ensure_ascii=True)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def package_tasks_command(argv: list[str]) -> int:
    from sapi_config_lab.coordinate.packages import stage_tasks

    options = argparse.ArgumentParser(description="Assemble disposable Harbor task packages.")
    options.add_argument("destination", type=Path)
    options.add_argument(
        "--mode",
        choices=["oracle", "generation"],
        default="oracle",
        help=(
            "stage_tasks also has a replay mode, deliberately not offered here: a replay package is "
            "only meaningful with a validated submissions manifest and the image that run freezes. "
            "Use 'sapi-lab live --submissions-manifest' for it."
        ),
    )
    options.add_argument("--image", default=LAB_IMAGE)
    options.add_argument("--scenario", action="append", dest="scenarios")
    options.add_argument("--catalog", choices=["full", "scenario"], default="full", help="generation catalog arm")
    selected = options.parse_args(argv)
    stage_tasks(
        selected.destination,
        mode=selected.mode,
        image=selected.image,
        scenarios=tuple(selected.scenarios) if selected.scenarios else None,
        catalog=selected.catalog,
    )
    print(selected.destination)
    return 0


def fetch_source_command(argv: list[str]) -> int:
    from sapi_config_lab.pinned_source import fetch_source, pinned_source

    parser = argparse.ArgumentParser(description="Fetch a source pinned by provenance/<name>-source.json.")
    parser.add_argument("name", help="e.g. autowfbench")
    print(fetch_source(pinned_source(parser.parse_args(argv).name)))
    return 0


def dispatch(argv: list[str] | None = None, *, backend: WorkflowBackend | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sapiens YAML research lab.",
        epilog=command_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", metavar="command", choices=[*PUBLIC, *INTERNAL])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command == "benchmarks":
        return benchmarks_command(args.arguments)
    if command == "check":
        return check_command(args.arguments)
    if command == "compile":
        return compile_command(args.arguments, backend=backend)
    if command == "build":
        return build_command(args.arguments, backend=backend)
    if command == "execute":
        from sapi_config_lab.coordinate.cases import main as execute_command

        return execute_command(args.arguments, backend=backend)
    if command == "package-tasks":
        return package_tasks_command(args.arguments)
    if command == "fetch-source":
        return fetch_source_command(args.arguments)
    module = importlib.import_module("sapi_config_lab." + MODULES[command])
    previous = sys.argv
    try:
        sys.argv = [f"sapi-lab {command}", *args.arguments]
        return module.main()
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None, *, backend: WorkflowBackend | None = None) -> int:
    try:
        return dispatch(argv, backend=backend)
    except (Invalid, Unsupported) as error:
        print(json.dumps({"error": {"type": type(error).__name__, "message": str(error)}}), file=sys.stderr)
        return 2
