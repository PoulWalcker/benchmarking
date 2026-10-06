"""Public commands. Import optional experiment dependencies only when needed."""

import argparse
import importlib
import json
from pathlib import Path
import sys

from sapi_config_lab.evidence import write_json
from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.profile import read, read_bindings, validate, Invalid, Unsupported
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.contracts import CompileOptions, WorkflowBackend

# Commands dispatched by importing a module and calling its main(). The four
# remaining names are handled inline below.
MODULES = {
    "harbor": "coordinate.controls",
    "benchmark": "coordinate.benchmark",
    "benchmark-series": "evaluate.benchmark_series",
    "benchmark-calibrate": "evaluate.judge_calibration",
    "live": "coordinate.live",
    "generate": "coordinate.generate",
    "bridge": "execute.agency",
    "transport": "coordinate.transport",
    "ui": "coordinate.ui",
    "review-export": "evaluate.review_export",
    "lifecycle": "coordinate.lifecycle",
    "checkout-worker": "coordinate.benchmark_worker",
}

# What a person types at a normal checkout.
PUBLIC = {
    "compile": "Validate one YAML; write the n8n JSON and its step-to-node map.",
    "build": "Compile every benchmarks/*/config.yaml and record which were rejected.",
    "harbor": "Unpaid control suite: pinned image, transport probes, oracle and nop trials.",
    "generate": "Model-authored YAML after the control suite passes. Costs model calls.",
    "live": "Replay frozen submissions against live operations. Costs model calls.",
    "benchmark": "AutoWFBench evaluation, --task checkout|crm. --mode live costs model calls.",
    "benchmark-series": "Build a comparison manifest from existing reports; reruns nothing.",
    "benchmark-calibrate": "Judge against frozen controls. Costs one judge call unless --prepare-only.",
    "ui": "Import graphs into local n8n and prepare one bounded manual session.",
    "lifecycle": "The durable lifecycle controller against a registry directory.",
    "review-export": "Write a derived analysis.md beside recorded evaluations; adds files only.",
}

# Entry points for a container or for another command. They run, but a host
# checkout is the wrong place to type them: each needs an environment it does
# not get here.
INTERNAL = {
    "execute": "Run one config through the engine. Needs the real n8n CLI on PATH.",
    "package-tasks": "Assemble Harbor task packages into a new directory; runs nothing.",
    "transport": "HTTP transport probes. Runs inside the lab image; run.sh invokes it there.",
    "bridge": "Foreground Agency HTTP adapter. live and ui start it themselves.",
    "checkout-worker": "Trusted verifier inside the task container; needs /tests and /logs.",
}


def command_help() -> str:
    """Render the two tiers for the epilog; argparse must not reflow this."""
    rows = ["commands you run:"]
    rows += [f"  {name:<21}{text}" for name, text in PUBLIC.items()]
    rows += ["", "internal commands (a container or another command runs these, not you):"]
    rows += [f"  {name:<21}{text}" for name, text in INTERNAL.items()]
    rows += ["", "Every command accepts --help."]
    return "\n".join(rows)


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
    root, rows = workspace_root(), []
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bindings = read_bindings(CATALOG)
    selected = backend if backend is not None else default_backend()
    for path in sorted((root / "benchmarks").glob("*/config.yaml")):
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
            compiled = selected.compile(cfg, bindings, CompileOptions())
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
    options.add_argument("--image", default="sapi-config-lab-n8n:2.41.5")
    options.add_argument("--scenario", action="append", dest="scenarios")
    selected = options.parse_args(argv)
    stage_tasks(
        selected.destination,
        mode=selected.mode,
        image=selected.image,
        scenarios=tuple(selected.scenarios) if selected.scenarios else None,
    )
    print(selected.destination)
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
    if command == "compile":
        return compile_command(args.arguments, backend=backend)
    if command == "build":
        return build_command(args.arguments, backend=backend)
    if command == "execute":
        from sapi_config_lab.coordinate.cases import main as execute_command

        return execute_command(args.arguments, backend=backend)
    if command == "package-tasks":
        return package_tasks_command(args.arguments)
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
