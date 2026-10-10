"""Checkout's exact authoring prompt and trusted experiment composition."""

import json
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from environment.hooks import plan
    from evaluation.evaluator import evaluate
elif __package__:
    from .environment.hooks import plan
    from .evaluation.evaluator import evaluate
else:
    from environment.hooks import plan
    from evaluation.evaluator import evaluate

ROOT = Path(__file__).resolve().parent


def runtime_options(request: dict) -> dict:
    native_mode = request.get("native_mode", "live" if request.get("mode") == "live" else "admission")
    mode = "live" if native_mode == "live" else "stub"
    if native_mode == "control":
        mode = request.get("mode", "stub")
    return {
        "mode": mode,
        "native_mode": native_mode,
        "deadline_seconds": 120,
        "selected_case": request.get("selected_case"),
        "judge_mode": "codex" if native_mode == "live" else "demo",
        "judge_model": request.get("judge_model") if native_mode == "live" else None,
    }


if __name__ == "__main__":
    from sapi_config_lab.coordinate.native_tasks import require_verifier_phase
    from sapi_config_lab.execute.n8n import execution_ceiling

    request = json.load(sys.stdin)
    result: dict
    if request["action"] in {"prompt", "plan"}:
        require_verifier_phase(ROOT, execution_ceiling(120, bound=False) + 120)
    if request["action"] == "prompt" and request["catalog"] == "full":
        result = {"prompt": (ROOT / "public/authoring-prompt.txt").read_text()}
    elif request["action"] == "plan":
        options = runtime_options(request["options"])
        result = {"options": options, "plan": plan(Path(request["submission"]), options)}
    elif request["action"] == "evaluate":
        from sapi_config_lab.coordinate.native_evaluation import evaluate_record

        try:
            result = evaluate_record(ROOT, evaluate, request)
        except subprocess.TimeoutExpired:
            raise SystemExit(124) from None
    else:
        raise ValueError("Unknown checkout experiment action or catalog")
    print(json.dumps(result))
