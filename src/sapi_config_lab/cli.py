"""Public commands. Import optional experiment dependencies only when needed."""

import argparse
import importlib
import json
from pathlib import Path
import sys

from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab.workflow.profile import read, read_bindings, validate, Invalid, Unsupported
from sapi_config_lab.runtime.composition import default_backend
from sapi_config_lab.runtime.contracts import CompileOptions, WorkflowBackend


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
    args.output.write_text(json.dumps(compiled.document, ensure_ascii=False, indent=2) + "\n")
    args.output.with_suffix(".map.json").write_text(json.dumps(compiled.mapping, indent=2) + "\n")
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
    for path in sorted((root / "configs").glob("*.yaml")):
        cfg = read(path)
        order, _ = validate(cfg, bindings)
        row = {
            "config": path.name,
            "valid_profile": True,
            "steps": len(order),
            "runtime_tested": False,
            "engine": selected.name,
        }
        try:
            compiled = selected.compile(cfg, bindings, CompileOptions())
            out = args.output_dir / (path.stem + "." + selected.name + ".json")
            out.write_text(json.dumps(compiled.document, ensure_ascii=False, indent=2) + "\n")
            out.with_suffix(".map.json").write_text(json.dumps(compiled.mapping, indent=2) + "\n")
            row.update(demo_export="generated", artifact=out.name)
        except Unsupported as error:
            row.update(demo_export="rejected", reason=str(error))
        rows.append(row)
    (args.output_dir / "build-results.json").write_text(json.dumps(rows, indent=2) + "\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def dispatch(argv: list[str] | None = None, *, backend: WorkflowBackend | None = None) -> int:
    commands = {
        "harbor": "experiments.harbor",
        "checkout": "experiments.checkout",
        "benchmark": "experiments.checkout",
        "benchmark-series": "experiments.benchmark_series",
        "benchmark-calibrate": "experiments.judge_calibration",
        "live": "experiments.live",
        "generate": "experiments.generation.run",
        "bridge": "runtime.agency",
        "transport": "experiments.transport",
        "ui": "experiments.ui",
        "review-export": "experiments.review_export",
        "lifecycle": "runtime.lifecycle",
    }
    parser = argparse.ArgumentParser(description="Sapiens YAML research lab. Commands accept --help.")
    parser.add_argument("command", choices=["compile", "build", "execute", "package-tasks", *commands])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "compile":
        return compile_command(args.arguments, backend=backend)
    if args.command == "build":
        return build_command(args.arguments, backend=backend)
    if args.command == "execute":
        from sapi_config_lab.runtime.execution import main as execute_command

        return execute_command(args.arguments, backend=backend)
    if args.command == "package-tasks":
        from sapi_config_lab.experiments.tasks import stage_tasks

        options = argparse.ArgumentParser(description="Assemble disposable Harbor task packages.")
        options.add_argument("destination", type=Path)
        options.add_argument("--mode", choices=["oracle", "generation"], default="oracle")
        options.add_argument("--image", default="sapi-config-lab-n8n:2.41.5")
        options.add_argument("--scenario", action="append", dest="scenarios")
        selected = options.parse_args(args.arguments)
        stage_tasks(
            selected.destination,
            mode=selected.mode,
            image=selected.image,
            scenarios=tuple(selected.scenarios) if selected.scenarios else None,
        )
        print(selected.destination)
        return 0
    module = importlib.import_module("sapi_config_lab." + commands[args.command])
    previous = sys.argv
    try:
        sys.argv = [f"sapi-lab {args.command}", *args.arguments]
        return module.main()
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None, *, backend: WorkflowBackend | None = None) -> int:
    try:
        return dispatch(argv, backend=backend)
    except (Invalid, Unsupported) as error:
        print(json.dumps({"error": {"type": type(error).__name__, "message": str(error)}}), file=sys.stderr)
        return 2
