"""Native task composition; the original invoice evaluator owns all fixtures and verdicts."""

import json
import os
from pathlib import Path

from payload.evaluation.evaluator import evaluate, plan

from sapi_config_lab.coordinate.benchmark_worker import run_task

ROOT = Path("/tests/payload")
OUT = Path("/logs/verifier")
SUBMISSION = Path("/submission/config.yaml")

if __name__ == "__main__":
    raise SystemExit(
        run_task(
            ROOT,
            OUT,
            SUBMISSION,
            {
                "mode": os.environ.get("SAPI_LLM_MODE", "stub"),
                "deadline_seconds": int(os.environ.get("SAPI_NATIVE_DEADLINE_SECONDS", "30")),
                "selected_case": os.environ.get("SAPI_CASE_NAME"),
                **({"cases": json.loads(os.environ["SAPI_NATIVE_CASES"])} if "SAPI_NATIVE_CASES" in os.environ else {}),
                "submission": str(SUBMISSION),
                "evaluation": str(OUT / "evaluation"),
            },
            {"plan": plan, "evaluate": evaluate},
        )
    )
