"""Native research report composition over real n8n evidence."""

import json
import os
from pathlib import Path

from payload.evaluation.evaluator import evaluate, plan
from payload.experiment import runtime_options

from sapi_config_lab.coordinate.native_record import record
from sapi_config_lab.coordinate.task_worker import run_task

ROOT = Path("/tests/payload")
OUT = Path("/logs/verifier")
SUBMISSION = Path("/submission/config.yaml")


def main() -> int:
    mode = os.environ.get("SAPI_LLM_MODE", "stub")
    options = record(
        "research-report",
        OUT,
        SUBMISSION,
        runtime_options(
            {
                "mode": mode,
                "deadline_seconds": int(
                    os.environ.get("SAPI_NATIVE_DEADLINE_SECONDS", "600" if mode == "live" else "120")
                ),
                "selected_case": os.environ.get("SAPI_CASE_NAME"),
                "judge_model": os.environ.get("SAPI_NATIVE_JUDGE_MODEL"),
                **({"cases": json.loads(os.environ["SAPI_NATIVE_CASES"])} if "SAPI_NATIVE_CASES" in os.environ else {}),
            }
        ),
    )
    return run_task(ROOT, OUT, SUBMISSION, options, {"plan": plan, "evaluate": evaluate})


if __name__ == "__main__":
    raise SystemExit(main())
