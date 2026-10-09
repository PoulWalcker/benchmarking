"""Invoice's exact authoring prompts and trusted experiment composition."""

import json
from pathlib import Path
import subprocess
import sys

if __package__:
    from .evaluation.evaluator import evaluate, plan
else:
    from evaluation.evaluator import evaluate, plan
import yaml

from sapi_config_lab.profile import read

ROOT = Path(__file__).resolve().parent


def runtime_options(request: dict) -> dict:
    mode = request.get("mode", "stub")
    return {
        "mode": mode,
        "deadline_seconds": request.get("deadline_seconds", 600 if mode == "live" else 30),
        "selected_case": request.get("selected_case"),
        **({"cases": request["cases"]} if "cases" in request else {}),
    }


def authoring(catalog: str) -> dict:
    if catalog not in {"full", "scenario"}:
        raise ValueError("Unknown invoice catalog")
    text = (ROOT / "bindings.yaml").read_text()
    if catalog == "scenario":
        used = {step["uses"] for step in read(ROOT / "solution/config.yaml")["workflow"]["steps"]}
        kept, section, keep = [], "", True
        for line in text.splitlines(keepends=True):
            if line[:1].isalpha():
                section, keep = line.split(":", 1)[0], True
            elif section == "operations" and line.startswith("  ") and line[2:3].isalpha():
                keep = line.strip().removesuffix(":") in used
            if keep:
                kept.append(line)
        reduced = "".join(kept)
        full = yaml.safe_load(text)
        if yaml.safe_load(reduced) != {
            **full,
            "operations": {k: v for k, v in full["operations"].items() if k in used},
        }:
            raise ValueError("Catalog reduction changed operation declarations")
        text = reduced
    prompt = (
        "TASK\n"
        + (ROOT / "task.md").read_text().removesuffix("\n")
        + "\n\nFORMAT\n"
        + (ROOT.parent.parent / "generation/FORMAT.md").read_text()
        + "\n\nPROFILE\n"
        + (ROOT.parent.parent / "generation/PROFILE.md").read_text()
        + "\n\nOPERATION CATALOG\n"
        + text
    )
    return {"prompt": prompt, "catalog": text, "cases": json.loads((ROOT / "cases.json").read_text())}


if __name__ == "__main__":
    from sapi_config_lab.coordinate.native_tasks import require_verifier_phase
    from sapi_config_lab.execute.n8n import execution_ceiling

    request = json.load(sys.stdin)
    if request["action"] == "prompt":
        result = authoring(request["catalog"])
        count = len(plan(ROOT / "solution/config.yaml", {"deadline_seconds": 120})["entries"])
        require_verifier_phase(
            ROOT, max(count * execution_ceiling(120, bound=False), execution_ceiling(600, bound=False)) + 120
        )
    elif request["action"] == "plan":
        from sapi_config_lab.coordinate.provenance import source_manifest
        from sapi_config_lab.evidence import digest

        options = runtime_options(request["options"])
        result = {
            "options": options,
            "plan": plan(
                Path(request["submission"]),
                {**options, "identity": {"task": ROOT.name, "sources_sha256": digest(source_manifest())}},
            ),
        }
        require_verifier_phase(
            ROOT,
            max(
                len(result["plan"]["entries"]) * execution_ceiling(options["deadline_seconds"], bound=False),
                execution_ceiling(600, bound=False),
            )
            + 120,
        )
    elif request["action"] == "evaluate":
        from sapi_config_lab.coordinate.native_evaluation import evaluate_record

        try:
            result = evaluate_record(ROOT, evaluate, request)
        except subprocess.TimeoutExpired:
            raise SystemExit(124) from None
    else:
        raise ValueError("Unknown invoice experiment action")
    print(json.dumps(result))
