"""Research report's frozen authoring contract and native experiment entrypoint."""

import json
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evaluation.evaluator import card, evaluate, plan
elif __package__:
    from .evaluation.evaluator import card, evaluate, plan
else:
    from evaluation.evaluator import card, evaluate, plan

ROOT = Path(__file__).resolve().parent


def runtime_options(request: dict) -> dict:
    mode = request.get("mode", "stub")
    judge_model = request.get("judge_model")
    if judge_model is not None and (not isinstance(judge_model, str) or not judge_model.strip()):
        raise ValueError("A research Judge model must be a nonempty name")
    return {
        "mode": mode,
        "deadline_seconds": request.get("deadline_seconds", 600 if mode == "live" else 120),
        "selected_case": request.get("selected_case"),
        **({"cases": request["cases"]} if "cases" in request else {}),
        # An identity is frozen, never a dispatch grant; unpaid records stay identity-free.
        **({"judge_mode": "wrapper", "judge_model": judge_model} if judge_model else {}),
    }


def authoring(catalog: str) -> dict:
    if catalog != "full":
        raise ValueError("Only the research-report full catalog is supported")
    prompt = (
        "TASK\n"
        + (ROOT / "task.md").read_text().removesuffix("\n")
        + "\n\nFORMAT\n"
        + (ROOT.parent.parent / "generation/FORMAT.md").read_text()
        + "\n\nPROFILE\n"
        + (ROOT.parent.parent / "generation/PROFILE.md").read_text()
        + "\n\nOPERATION CATALOG\n"
        + (ROOT / "bindings.yaml").read_text()
    )
    return {"prompt": prompt, "cases": json.loads((ROOT / "cases.json").read_text())}


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
        from sapi_config_lab.coordinate.fixture_judge import FixtureJudge
        from sapi_config_lab.coordinate.native_evaluation import evaluate_record
        from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper

        def judge_factory(*, directory, native_identity, options, reserved=None, wrapper=None, saved=None):
            # The only Judge this root composes: an exact saved replay or one reserved inspected-wrapper call.
            if saved is not None:
                return FixtureJudge.saved(
                    saved,
                    native_identity,
                    card(),
                    options["judge_model"],
                    replay_receipt=directory / "replay-receipt.json",
                )
            return FixtureJudge.fresh(
                directory,
                native_identity,
                card(),
                options.get("judge_model"),
                reserved=reserved,
                transport=request_wrapper,
                **wrapper,
            )

        try:
            result = evaluate_record(ROOT, evaluate, request, judge_factory=judge_factory)
        except subprocess.TimeoutExpired:
            raise SystemExit(124) from None
    else:
        raise ValueError("Unknown research-report experiment action")
    print(json.dumps(result))
