"""Offline evaluation of explicitly supplied frozen independent evaluator snapshots."""

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.evaluate.records import quality, validate_result, verifier_result
from sapi_config_lab.evidence import digest, sha256
from sapi_config_lab.pinned_source import PinnedSource


def load_snapshot(path: Path, name: str, *, package: bool = False) -> Any:
    """Load a verified user-selected snapshot in a namespace unique to its source and location."""
    name = "_sapi_historical_" + digest([str(path.resolve()), name, sha256(path)])
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if package else None
    )
    if spec is None or spec.loader is None:
        raise ValueError("Historical evaluator snapshot cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(name + ".verify") if package else module


def reevaluate_record(
    record: Path,
    output: Path,
    root: Path | None,
    manifest_path: Path | None,
    cases_path: Path | None,
    judgement_path: Path | None,
) -> dict:
    """Refuse missing or changed sources before loading an evaluator; never start a world or judge."""
    if root is None or manifest_path is None:
        raise ValueError(
            "Historical evaluator source snapshot unavailable: supply --source-root and --source-manifest; "
            "ordinary recorded report reads remain available"
        )
    root = root.resolve()
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Historical source manifest must contain frozen file hashes")
    hosted = (record / "evidence/trial.json").is_file()
    prefix = "src/sapi_config_lab/evaluate/" if hosted else "verification/"
    declared = {relative: expected for relative, expected in manifest.items() if relative.startswith(prefix)}
    if not hosted:
        declared = {
            relative: expected
            for relative, expected in manifest.items()
            if relative.startswith(("verification/", "benchmarks/"))
        }
    if not declared:
        raise ValueError("Historical independent evaluator is not declared in the frozen source manifest")
    captured = {}
    for relative, expected in declared.items():
        path = root / relative
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError("Historical source snapshot unavailable: " + relative)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Historical source snapshot differs: " + relative)
        captured[relative] = content
    if not hosted:
        for path in (root / "verification").glob("*.py"):
            if path.relative_to(root).as_posix() not in declared:
                raise ValueError("Undeclared historical independent evaluator source: " + path.name)
    original_root = root
    # Fresh captured bytes prevent stale modules or unchecked bytecode from substituting another evaluator.
    with tempfile.TemporaryDirectory(prefix="sapi-historical-") as temporary:
        root = Path(temporary)
        for relative, content in captured.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if (record / "evidence/trial.json").is_file():
            adapter = root / "src/sapi_config_lab/evaluate/autowfbench.py"
            if adapter.relative_to(root).as_posix() not in manifest:
                raise ValueError("Historical independent evaluator is not declared in the frozen source manifest")
            recorded = json.loads((record / "evaluation/task-contract.json").read_text())
            if recorded.get("schema") != "sapi-lab-task-contract/v1":
                raise ValueError("Unsupported historical evaluator contract")
            contract_digest = hashlib.sha256(
                json.dumps(
                    {k: v for k, v in recorded.items() if k != "contract_digest"},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if contract_digest != recorded["contract_digest"]:
                raise ValueError("Historical task contract digest differs")
            source_manifest = original_root / "provenance/autowfbench-source.json"
            source = PinnedSource(
                source_manifest, original_root / ".cache/autowfbench" / recorded["source"]["revision"]
            )
            if source.verify() != recorded["source"]:
                raise ValueError("Historical independent evaluator source identity differs")
            evaluator = load_snapshot(adapter, "hosted")
            trial = json.loads((record / "evidence/trial.json").read_text())
            acceptance = json.loads((record / "evaluation/evaluation.json").read_text()).get("project_acceptance") or {}
            artifact = (
                OutputArtifact("recorded_artifact", acceptance["artifact"])
                if acceptance.get("criterion") == "named_artifact"
                else None
            )
            contract = evaluator.FrozenTaskContract(
                source,
                json.dumps(recorded["package"], sort_keys=True),
                json.dumps(recorded["source"], sort_keys=True),
                recorded["judge"]["model"],
                recorded["judge"]["mode"],
                recorded["judge"]["prompt_version"],
                artifact,
            )
            contract.verify()
            if contract.as_dict() != recorded:
                raise ValueError("Historical evaluator or judge identity differs")
            run_log = evaluator.recorded_run_log(contract, record / "evidence")
            judgement = json.loads(judgement_path.read_text()) if judgement_path else None
            evaluation = evaluator.evaluate_once(contract, run_log, output / "evaluation", judgement=judgement)
            return validate_result(
                {
                    "execution": trial["termination_reason"] == "completed",
                    "acceptance": evaluation.get("execution_pass") is True,
                    "quality": quality(evaluation),
                }
            )
        if judgement_path is not None:
            raise ValueError("Historical fixture evaluator does not support judge options")
        plan = json.loads((record / "plan.json").read_text())
        scenario = plan["scenario"]
        if not isinstance(scenario, str) or re.fullmatch(r"[a-z][a-z0-9-]*", scenario) is None:
            raise ValueError("Invalid historical fixture identity")
        matches = sorted((root / "benchmarks").glob(f"[0-9][0-9]-{scenario}"))
        if len(matches) != 1:
            raise ValueError("Historical fixture snapshot unavailable: " + scenario)
        benchmark = matches[0]
        verifier_root = root / "verification"
        sources = {path.name: sha256(path) for path in sorted(verifier_root.glob("*.py"))}
        for path in verifier_root.glob("*.py"):
            if path.relative_to(root).as_posix() not in manifest:
                raise ValueError("Undeclared historical independent evaluator source: " + path.name)
        for name in ("contract.json", "rubric.json"):
            path = benchmark / "evaluation" / name
            if path.is_file():
                sources["evaluation/" + name] = sha256(path)
        report = json.loads((record / "evaluation/report.json").read_text())
        identity = {"name": "sapi-lab-independent-verifier", "sources_sha256": digest(sources)}
        if identity != report.get("evaluator"):
            raise ValueError("Historical independent fixture evaluator identity differs")
        evaluator = load_snapshot(verifier_root / "__init__.py", "fixture", package=True)
        cases = json.loads((cases_path or benchmark / "cases.json").read_text())
        cases = cases.get(scenario, cases)
        selected = plan["entries"][0]["name"] if plan["mode"] == "live" and len(plan["entries"]) == 1 else None
        result = evaluator.evaluate(
            scenario,
            record / "evidence/submission.yaml",
            record / "evidence",
            cases,
            plan["mode"],
            selected,
            evaluation=output / "evaluation",
        )
        rubric = output / "evaluation/evaluation.json"
        return verifier_result(result, json.loads(rubric.read_text()) if rubric.exists() else None)
