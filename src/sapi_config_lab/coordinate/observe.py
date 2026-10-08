"""Run a verifier-issued observation plan and record what the engine did."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from sapi_config_lab.contracts import ArtifactTransform, Document, ExecutionRecord
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.evidence import digest, durable_json, sha256, write_json
from sapi_config_lab.paths import CATALOG
from sapi_config_lab.profile import read_bindings

Runner = Callable[..., ExecutionRecord]


def wrong_result(artifact: Document) -> Document:
    """Alter the compiled Result node so n8n succeeds with a wrong output."""
    for node in artifact["nodes"]:
        if node["name"] == "Result":
            original = node["parameters"]["jsCode"]
            node["parameters"]["jsCode"] = (
                "const result = await (async () => {\n"
                + original
                + "\n})();\nresult[0].json.output = {deliberately_wrong: true};\nreturn result;"
            )
    return artifact


TRANSFORMS: dict[str, ArtifactTransform] = {"wrong-result": wrong_result}


def _case(
    entry: Document, directory: Path, mode: str, bridge_url: str | None, runner: Runner, bindings: Document
) -> None:
    transform = entry.get("artifact_transform")
    options: dict = {"llm_mode": mode, "bridge_url": bridge_url}
    if transform is not None:
        options = {"llm_mode": mode, "artifact_transform": TRANSFORMS[transform]}
    runner(entry["config"], directory, bindings=bindings, **options)


def _lifecycle(entry: Document, directory: Path, backend, bindings: Document, acceptance) -> None:
    from sapi_config_lab.coordinate.lifecycle import LifecycleController

    controller = LifecycleController(directory, backend=backend, bindings=bindings, verifier=acceptance)
    event = controller.callback(controller.register(entry["config"]), entry["callback"])
    durable_json(directory / "event.json", event)
    if entry["tick"]:
        controller.tick(datetime.fromisoformat(entry["tick"]))
    durable_json(directory / "snapshot.json", controller.snapshot())


def recorded_files(directory: Path) -> dict[str, str]:
    """Every file an entry left behind, by path relative to its directory."""
    return {
        path.relative_to(directory).as_posix(): sha256(path) for path in sorted(directory.rglob("*")) if path.is_file()
    }


def observe(
    plan: Document,
    submission: Path,
    evidence: Path,
    *,
    bridge_url: str | None = None,
    runner: Runner = run_case,
    backend=None,
    bindings: Document | None = None,
    acceptance: Callable[[Document, ExecutionRecord], Document] | None = None,
) -> Document:
    """Execute every plan entry in order and write observation.json last."""
    evidence.mkdir(parents=True, exist_ok=True)
    bindings = read_bindings(CATALOG) if bindings is None else bindings
    shutil.copyfile(submission, evidence / "submission.yaml")
    manifest: Document = {
        "schema": "sapi-lab-observation/v1",
        "plan_sha256": digest(plan),
        "entries": [],
    }
    for entry in plan["entries"]:
        directory = evidence / "cases" / entry["name"]
        row: Document = {"name": entry["name"], "files": {}}
        try:
            if entry["procedure"] == "lifecycle":
                _lifecycle(entry, directory, backend, bindings, acceptance)
            else:
                _case(entry, directory, plan["mode"], bridge_url, runner, bindings)
        except Exception as error:  # A failed entry is evidence too; acceptance decides
            row["error"] = f"{type(error).__name__}: {error}"
        row["files"] = recorded_files(directory) if directory.is_dir() else {}
        manifest["entries"].append(row)
    write_json(evidence / "observation.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    from sapi_config_lab.coordinate.legacy_lifecycle import digest_acceptance

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--bridge-url", default=os.environ.get("SAPI_BRIDGE_URL"))
    parser.add_argument("--bindings", type=Path, default=CATALOG)
    args = parser.parse_args(argv)
    manifest = observe(
        json.loads(args.plan.read_text()),
        args.submission,
        args.evidence,
        bridge_url=args.bridge_url,
        bindings=read_bindings(args.bindings),
        acceptance=digest_acceptance,
    )
    print(json.dumps({"observed": len(manifest["entries"]), "errors": sum("error" in r for r in manifest["entries"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
