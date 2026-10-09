"""Native task composition; the original invoice evaluator owns all fixtures and verdicts."""

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
                "mode": "stub",
                "deadline_seconds": 30,
                "submission": str(SUBMISSION),
                "evaluation": str(OUT / "evaluation"),
            },
            {"plan": plan, "evaluate": evaluate},
        )
    )
