"""One result shape over the independent verifier and each hosted evaluator.

All read recorded evidence and report execution, acceptance and an optional
quality score (null is never zero); they share no scoring semantics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

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


def _require_fresh(output: Path, series_dir: Path | None, model: str, inspection: Path, quality: object) -> None:
    """Prove from the standalone ledger and receipt, not the task's exit, that this command's dispatch happened."""
    from sapi_config_lab.coordinate import fixture_judge

    ledger = (series_dir or output) / "ledger.json"
    events = json.loads(ledger.read_text())["events"] if ledger.is_file() else []
    name, report = f"{output.name}/judge", str((output / "result.json").resolve())
    found = [i for i, event in enumerate(events) if event["name"] == name and event["report"] == report]
    if len(found) != 1 or events[found[0]]["status"] != "passed":
        raise ValueError("Requested fresh Judge has no passed reservation")
    # The receipt was stamped with the pending event; closing a reservation changes only its status.
    pending = {**events[found[0]], "status": "unknown"}
    fixture_judge.require_fresh(
        output / "judge", ledger, found[0], pending, model=model, inspection=inspection, quality=quality
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one recorded trial again into a new directory.")
    parser.add_argument("--record", type=Path, required=True, help="<trial>/verifier, or environments/<job>/<scenario>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--judgement", type=Path, help="A saved judge reply or bundle for exactly this judge and run")
    parser.add_argument(
        "--dispatch-judge", action="store_true", help="Call the judge once; nothing is called without it"
    )
    parser.add_argument("--calibration", help="Judge a predefined counterfactual of the recorded run instead")
    parser.add_argument("--judge-model", help="Judge identity; must equal a frozen one, never the runtime model")
    parser.add_argument("--judge-upstream", help="Fixture Judge wrapper URL; host-owned, separate from the runtime")
    parser.add_argument("--judge-wrapper-evidence", type=Path, help="The inspected Judge wrapper identity record")
    parser.add_argument(
        "--judge-wrapper-file", action="append", help="NAME=PATH: where an inspected Judge wrapper file is here"
    )
    parser.add_argument("--series-dir", type=Path, help="Reserve the judge call in a shared ledger")
    parser.add_argument("--series-ceiling", action="append")
    args = parser.parse_args(argv)
    from sapi_config_lab.coordinate.wrapper import parse_wrapper_files

    if args.calibration and not args.judge_model:
        parser.error("--calibration needs --judge-model")
    if args.judgement and args.dispatch_judge:
        parser.error("Supply a saved judgement or dispatch the judge, not both")
    try:
        judge_files = parse_wrapper_files(args.judge_wrapper_file)
    except ValueError as error:
        parser.error(str(error).replace("--wrapper-file", "--judge-wrapper-file"))
    if (args.judge_upstream is None) != (args.judge_wrapper_evidence is None) or (
        judge_files and args.judge_wrapper_evidence is None
    ):
        parser.error("--judge-upstream and --judge-wrapper-evidence name one inspected Judge wrapper together")
    if args.judge_upstream and not (args.dispatch_judge and args.judge_model):
        parser.error("A Judge endpoint needs --dispatch-judge and an explicit --judge-model")
    record = args.record.resolve()
    output = args.output.resolve()
    if output.is_relative_to(record / "evidence"):
        raise ValueError("Derived evaluation output must be outside recorded evidence")
    if not (record / "native-task.json").is_file():
        raise ValueError("Historical evaluator execution is deferred; recorded results remain readable")
    from sapi_config_lab.coordinate.native_evaluation import reevaluate_native

    series_dir = args.series_dir.resolve() if args.series_dir else None
    endpoint = (
        {
            "upstream": args.judge_upstream,
            "inspection": str(args.judge_wrapper_evidence.resolve()),
            "files": {name: str(path.resolve()) for name, path in judge_files.items()},
        }
        if args.judge_upstream
        else None
    )
    requested = bool(args.dispatch_judge or args.judgement or args.calibration)
    try:
        result = reevaluate_native(
            record,
            output,
            args.judgement,
            dispatch=args.dispatch_judge,
            calibration=args.calibration,
            judge_model=args.judge_model,
            judge=endpoint,
            series_dir=str(series_dir) if series_dir else None,
            series_ceiling=args.series_ceiling,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if not requested:
            raise
        return _judge_failed(output, error)
    if endpoint is not None:
        try:
            _require_fresh(output, series_dir, args.judge_model, args.judge_wrapper_evidence, result["quality"])
        except ValueError as error:
            return _judge_failed(output, error)
    print(json.dumps(result))
    judged = (result["quality"] or {}).get("status") == "complete"
    return 0 if result["acceptance"] and (judged or not requested) else 1


def _judge_failed(output: Path, error: Exception) -> int:
    """Report a requested Judge stage that failed or stayed unknown; recorded acceptance stays visible."""
    saved = output / "result.json"
    reason = (getattr(error, "stderr", None) or str(error)).strip().splitlines()
    print(
        json.dumps(
            {
                "judge": "unknown" if isinstance(error, subprocess.TimeoutExpired) else "failed",
                "reason": reason[-1] if reason else type(error).__name__,
                "result": json.loads(saved.read_text()) if saved.is_file() else None,
            }
        )
    )
    return 1
