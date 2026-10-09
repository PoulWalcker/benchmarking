"""Research report's frozen authoring contract and native experiment entrypoint."""

import json
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evaluation import calibration
    from evaluation.evaluator import card, evaluate, plan
elif __package__:
    from .evaluation import calibration
    from .evaluation.evaluator import card, evaluate, plan
else:
    from evaluation import calibration
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


def calibrate(request: dict) -> dict:
    """Judge one frozen synthetic variant of a verified native record; never a native verdict.

    Unknown variants, foreign or incomplete base evidence and identity mismatches refuse before any
    reservation. Without `dispatch` or a saved `judgement` nothing is judged.
    """
    from sapi_config_lab.coordinate import fixture_judge
    from sapi_config_lab.coordinate.ledger import open_ledger, parse_ceilings
    from sapi_config_lab.coordinate.native_evaluation import judge_endpoint, validate_record
    from sapi_config_lab.coordinate.native_tasks import policy
    from sapi_config_lab.coordinate.provenance import source_manifest
    from sapi_config_lab.evidence import digest, sha256, write_json
    from sapi_config_lab.harbor_integration.model_wrapper import request_wrapper

    name = request["variant"]
    calibration.variant_text(name)
    record, output = Path(request["record"]).resolve(), Path(request["output"]).resolve()
    sources = source_manifest()
    metadata = validate_record(record, ROOT, sources)
    if output.is_relative_to(record) or output.exists():
        raise ValueError("Calibration needs a new directory outside the native record")
    saved = Path(request["judgement"]).resolve() if request.get("judgement") else None
    dispatch = request.get("dispatch", False)
    if type(dispatch) is not bool or (saved is not None and dispatch):
        raise ValueError("Calibration takes a saved judgement or one explicit dispatch, not both")
    endpoint = judge_endpoint(request.get("judge"))
    if dispatch != (endpoint is not None):
        raise ValueError("A calibration dispatch needs exactly one inspected Judge endpoint")
    cost = policy(ROOT).get("judge_calls", 0)
    # The base record's frozen Judge is the calibrated one; a name or saved bundle can only agree with it.
    named = [metadata["options"].get("judge_model"), request.get("judge_model")]
    if saved is not None:
        named.append(fixture_judge.saved_model(saved))
    models = {model for model in named if model is not None}
    if len(models) > 1:
        raise ValueError("Calibration Judge identity differs from the frozen, named or saved one")
    model = next(iter(models), None)
    if dispatch and (model is None or cost != 1):
        raise ValueError("A calibration dispatch needs one Judge call for a frozen or named Judge model")
    options = {
        **metadata["options"],
        "identity": {"task": ROOT.name, "sources_sha256": digest(sources)},
        "submission": str(record / "evidence/submission.yaml"),
        "evaluation": str(output / "base-verification"),
    }
    output.mkdir(parents=True)
    judge_request, identity = calibration.request(record / "evidence", options, name)
    native_identity = (record / "native-task.json").read_bytes()
    reply = None
    if saved is not None:
        assert model is not None
        replay = fixture_judge.FixtureJudge.saved(
            saved, native_identity, card(), model, replay_receipt=output / "judge-replay-receipt.json"
        )
        reply = replay.judge(judge_request)
    elif dispatch:
        assert endpoint is not None
        receipts = output / "judge"

        def reserved(call):
            # A calibration series stops after any failed attempt; an unknown one already blocks the ledger.
            series = Path(request["series_dir"]) if request.get("series_dir") else None
            ceilings = parse_ceilings(request.get("series_ceiling"))
            ledger = open_ledger(output, series, {"judge": cost}, True, ceilings)
            with ledger.reserved("judge", f"{output.name}/judge", cost, output / "calibration.json", cost) as outcome:
                index = len(ledger.data["events"]) - 1
                proof = fixture_judge.dispatch_reserved(
                    receipts,
                    call,
                    ledger.path,
                    index,
                    ledger.data["events"][index],
                    model=model,
                    inspection_sha256=sha256(endpoint["inspection"]),
                )
                outcome.passed = True
                return proof

        judge = fixture_judge.FixtureJudge.fresh(
            receipts, native_identity, card(), model, reserved=reserved, transport=request_wrapper, **endpoint
        )
        with fixture_judge.unknown_while_unsettled(receipts):
            reply = judge.judge(judge_request)
    if source_manifest() != sources:
        raise ValueError("Calibration sources changed")
    result = {
        "schema": "sapi-lab-research-calibration/v1",
        "synthetic": True,
        # The rewritten report never ran; its native execution and acceptance are unmeasured, not inherited.
        "native": {"execution": None, "acceptance": None, "base_record": str(record)},
        "identity": identity,
        "comparison": calibration.compare(name, reply),
    }
    write_json(output / "calibration.json", result)
    return result


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

        if request.get("calibration"):
            # Counterfactual prose never reaches a native verdict, reward or quality.
            raise ValueError("Research calibration is the separate synthetic calibrate action")
        try:
            result = evaluate_record(ROOT, evaluate, request, judge_factory=judge_factory)
        except subprocess.TimeoutExpired:
            raise SystemExit(124) from None
    elif request["action"] == "calibrate":
        try:
            result = calibrate(request)
        except subprocess.TimeoutExpired:
            raise SystemExit(124) from None
    else:
        raise ValueError("Unknown research-report experiment action")
    print(json.dumps(result))
