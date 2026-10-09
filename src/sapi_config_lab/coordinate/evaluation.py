"""One result shape over the independent verifier and each hosted evaluator.

All read recorded evidence and report execution, acceptance and an optional
quality score (null is never zero); they share no scoring semantics.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import asdict
import json
from pathlib import Path

from sapi_config_lab.benchmark import Benchmark, discover_benchmarks
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
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
from sapi_config_lab.evidence import sha256, write_json
from sapi_config_lab.paths import workspace_root


def recorded_benchmark(name: str) -> Benchmark:
    """Select trusted local metadata; recorded names never nominate executable paths."""
    matches = [item for item in discover_benchmarks(workspace_root() / "tasks") if item.name == name]
    if len(matches) != 1:
        raise ValueError("Recorded versioned benchmark is unavailable")
    return matches[0]


def control_passed(
    agent: str, trial: dict, benchmark: Benchmark | None = None, *, reference_reward: float | None = None
) -> bool:
    """Gate controls using independent acceptance and the declared reference reward."""
    if (trial.get("acceptance") or {}).get("schema") == ADMISSION_REPORT:
        return False
    result = trial["result"]
    quality = result["quality"]
    reference_reward = benchmark.controls.reference_reward if benchmark is not None else reference_reward
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


def reevaluate_benchmark(
    record: Path,
    output: Path,
    judgement: Path | None,
    *,
    dispatch: bool = False,
    calibration: str | None = None,
    judge_model: str | None = None,
    reserved: Callable | None = None,
) -> dict:
    """Replay the selected recorded evaluator only after its complete source identity matches."""
    metadata = json.loads((record / "benchmark.json").read_text())
    benchmark = recorded_benchmark(metadata["name"])
    identity = freeze_identity(benchmark, metadata["options"])
    if json.loads(json.dumps(asdict(identity))) != metadata["identity"]:
        raise ValueError("Recorded benchmark source or options identity differs")
    root = workspace_root()
    for relative, expected in metadata["core_files"].items():
        source = root / ("src" if relative.startswith("sapi_config_lab/") else "") / relative
        if sha256(source) != expected:
            raise ValueError("Recorded trusted core source identity differs: " + relative)
    options = {
        **metadata["options"],
        **metadata.get("runtime_options", {}),
        "evaluation": str(output / "evaluation"),
        "submission": str(record / "evidence/submission.yaml"),
        "identity": {"benchmark": metadata["identity"], "core_files": metadata["core_files"]},
        "dispatch": dispatch,
    }
    contract = record / "evaluation/task-contract.json"
    if contract.is_file():
        options["contract"] = str(contract)
    if judgement is not None:
        options["judgement"] = str(judgement)
    if calibration is not None:
        options.update(calibration=calibration, judge_mode="codex", judge_model=judge_model)
    if dispatch:
        if benchmark.budgets.judge_calls == 0 or reserved is None:
            raise ValueError("Judge dispatch requires a declared cost and host reservation")
        options["reserved_judge"] = reserved
    return validate_result(load_entrypoints(benchmark, identity).evaluate(record / "evidence", options))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate one recorded trial again into a new directory.")
    parser.add_argument("--record", type=Path, required=True, help="<trial>/verifier, or environments/<job>/<scenario>")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, help="Verifier fixtures the trial ran with (its staged tests/cases.json)")
    parser.add_argument("--judgement", type=Path, help="A saved judge reply for exactly this judge and run")
    parser.add_argument("--dispatch-judge", action="store_true", help="Call the judge. A codex judge costs one call")
    parser.add_argument("--calibration", help="Judge a predefined counterfactual of the recorded run instead")
    parser.add_argument("--judge-model", help="Judge identity for --calibration")
    parser.add_argument("--series-dir", type=Path, help="Reserve the judge call in a shared ledger")
    parser.add_argument("--series-ceiling", action="append")
    parser.add_argument("--source-root", type=Path, help="Explicit matching archived evaluator source checkout")
    parser.add_argument("--source-manifest", type=Path, help="Frozen source hashes for the historical checkout")
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
    if (record / "native-task.json").is_file():
        from sapi_config_lab.coordinate.native_evaluation import reevaluate_native

        request = {
            "dispatch": args.dispatch_judge,
            "calibration": args.calibration,
            "judge_model": args.judge_model,
            "series_dir": str(args.series_dir.resolve()) if args.series_dir else None,
            "series_ceiling": args.series_ceiling,
        }
        if args.source_root is not None or args.source_manifest is not None:
            from sapi_config_lab.coordinate.archived_evaluation import invoke_native_snapshot

            result = invoke_native_snapshot(
                record,
                output,
                args.source_root,
                args.source_manifest,
                {**request, "judgement": str(args.judgement.resolve()) if args.judgement else None},
            )
        else:
            result = reevaluate_native(record, output, args.judgement, **request)
    elif (record / "benchmark.json").is_file():
        from sapi_config_lab.coordinate.archived_evaluation import reevaluate_versioned

        result = reevaluate_versioned(
            record,
            output,
            args.source_root,
            args.source_manifest,
            judgement=args.judgement,
            dispatch=args.dispatch_judge,
            calibration=args.calibration,
            judge_model=args.judge_model,
            series_dir=args.series_dir,
            series_ceiling=args.series_ceiling,
        )
    else:
        from sapi_config_lab.coordinate.historical_evaluation import reevaluate_record

        if (record / "native-sources.sha256").is_file():
            raise ValueError(
                "Phase 1 native evidence lacks complete research source/options identity; it remains readable but cannot use guarded native re-evaluation"
            )
        if args.dispatch_judge or args.calibration:
            raise ValueError(
                "Historical snapshots support offline re-evaluation only; no judge dispatch or calibration"
            )
        output.mkdir(parents=True, exist_ok=False)
        result = reevaluate_record(record, output, args.source_root, args.source_manifest, args.cases, args.judgement)
    write_json(output / "result.json", result)
    print(json.dumps(result))
    return 0 if result["acceptance"] else 1
