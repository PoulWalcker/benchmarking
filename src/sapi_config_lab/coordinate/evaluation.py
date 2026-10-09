"""One result shape over the independent verifier and each hosted evaluator.

All read recorded evidence and report execution, acceptance and an optional
quality score (null is never zero); they share no scoring semantics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sapi_config_lab.evaluate.records import (
    ADMISSION_REPORT as ADMISSION_REPORT,
)
from sapi_config_lab.evaluate.records import (
    HOSTED_REPORT as HOSTED_REPORT,
)
from sapi_config_lab.evaluate.records import (
    NOT_EVALUATED as NOT_EVALUATED,
)
from sapi_config_lab.evaluate.records import (
    quality as quality,
)
from sapi_config_lab.evaluate.records import (
    trial_accepted as trial_accepted,
)
from sapi_config_lab.evaluate.records import (
    trial_result as trial_result,
)
from sapi_config_lab.evaluate.records import (
    validate_result as validate_result,
)
from sapi_config_lab.evaluate.records import (
    verifier_result as verifier_result,
)


def control_passed(agent: str, trial: dict, *, reference_reward: float | None = None) -> bool:
    """Gate controls using independent acceptance and the declared reference reward."""
    if (trial.get("acceptance") or {}).get("schema") == ADMISSION_REPORT:
        return False
    result = trial["result"]
    quality = result["quality"]
    if agent == "oracle":
        expected = reference_reward
        if expected is not None and (quality or {}).get("status") != "complete":
            return False
        if expected is None:
            expected = (quality or {}).get("normalized_reward") if quality is not None else 1.0
        return (
            not trial["exception"]
            and result["acceptance"] is True
            and (trial["rewards"] == {"reward": expected} and (quality is None or quality.get("status") == "complete"))
        )
    if agent != "nop" or result["acceptance"] is not False:
        return False
    if quality is None and reference_reward is not None and trial["rewards"] is None:
        return (trial["exception"] or {}).get("exception_type") == "RewardFileNotFoundError"
    if quality is None or quality.get("status") == "complete":
        return not trial["exception"] and trial["rewards"] == {"reward": 0.0}
    if reference_reward is None and trial["rewards"] == {"reward": 0.0}:
        return not trial["exception"] and quality.get("score_0_10") is None and quality.get("normalized_reward") is None
    exception = trial["exception"] or {}
    return (
        quality.get("score_0_10") is None
        and quality.get("normalized_reward") is None
        and trial["rewards"] is None
        and (not exception or exception.get("exception_type") == "RewardFileNotFoundError")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one recorded trial again into a new directory.")
    parser.add_argument("--record", type=Path, required=True, help="<trial>/verifier, or environments/<job>/<scenario>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judgement", type=Path, help="A saved judge reply for exactly this judge and run")
    parser.add_argument("--dispatch-judge", action="store_true", help="Call the judge. A codex judge costs one call")
    parser.add_argument("--calibration", help="Judge a predefined counterfactual of the recorded run instead")
    parser.add_argument("--judge-model", help="Judge identity for --calibration")
    parser.add_argument("--series-dir", type=Path, help="Reserve the judge call in a shared ledger")
    parser.add_argument("--series-ceiling", action="append")
    args = parser.parse_args(argv)
    if args.calibration and not args.judge_model:
        parser.error("--calibration needs --judge-model")
    if args.judgement and args.dispatch_judge:
        parser.error("Supply a saved judgement or dispatch the judge, not both")
    record = args.record.resolve()
    hosted = (record / "evidence/trial.json").is_file()
    if not hosted and (args.judgement or args.dispatch_judge or args.calibration):
        parser.error("The independent verifier has no semantic judge")
    output = args.output.resolve()
    if output.is_relative_to(record / "evidence"):
        raise ValueError("Derived evaluation output must be outside recorded evidence")
    if not (record / "native-task.json").is_file():
        raise ValueError("Historical evaluator execution is deferred; recorded results remain readable")
    from sapi_config_lab.coordinate.native_evaluation import reevaluate_native

    result = reevaluate_native(
        record,
        output,
        args.judgement,
        dispatch=args.dispatch_judge,
        calibration=args.calibration,
        judge_model=args.judge_model,
        series_dir=str(args.series_dir.resolve()) if args.series_dir else None,
        series_ceiling=args.series_ceiling,
    )
    print(json.dumps(result))
    return 0 if result["acceptance"] else 1
