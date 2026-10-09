"""Re-evaluate a source-matched native record without repeating its execution."""

from collections.abc import Callable
import json
from pathlib import Path

from sapi_config_lab.coordinate import fixture_judge
from sapi_config_lab.coordinate.ledger import Ledger, open_ledger, parse_ceilings
from sapi_config_lab.coordinate.native_record import SCHEMA
from sapi_config_lab.coordinate.native_tasks import invoke, policy, select_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evaluate.records import validate_result
from sapi_config_lab.evidence import digest, sha256, write_json
from sapi_config_lab.paths import resource_root


def validate_record(record: Path, task: Path, sources: dict) -> dict:
    """Reject altered source, options or submission before trusted evaluation is invoked."""
    metadata = json.loads((record / "native-task.json").read_text())
    if not isinstance(metadata, dict) or set(metadata) != {
        "schema",
        "name",
        "sources",
        "options",
        "options_sha256",
        "submission_sha256",
    }:
        raise ValueError("Incomplete native record identity")
    if metadata.get("schema") != SCHEMA or metadata.get("name") != task.name or metadata.get("sources") != sources:
        raise ValueError("Native record source identity differs; evaluate with the exact recorded source revision")
    options = metadata.get("options")
    allowed = {"mode", "deadline_seconds", "selected_case", "cases", "judge_mode", "judge_model", "native_mode"}
    if not isinstance(options, dict) or options.keys() - allowed or metadata.get("options_sha256") != digest(options):
        raise ValueError("Native recorded options identity differs")
    if (
        options.get("mode") not in {"stub", "live"}
        or type(options.get("deadline_seconds")) is not int
        or options["deadline_seconds"] <= 0
        or ("native_mode" in options and options["native_mode"] not in {"control", "admission", "live"})
    ):
        raise ValueError("Invalid native recorded runtime options")
    judge = (options.get("judge_mode"), options.get("judge_model"))
    if ("judge_mode" in options or "judge_model" in options) and not (
        judge == ("demo", None) or (judge[0] in {"codex", "wrapper"} and isinstance(judge[1], str) and judge[1].strip())
    ):
        raise ValueError("Invalid native recorded Judge identity")
    submission = record / "evidence/submission.yaml"
    actual = sha256(submission) if submission.is_file() else None
    if "submission_sha256" not in metadata or actual != metadata["submission_sha256"]:
        raise ValueError("Native recorded submission identity differs")
    trial = record / "evidence/trial.json"
    if trial.is_file():
        observed = json.loads(trial.read_text())
        if observed.get("submission_sha256") != actual or observed.get("llm_mode") != options["mode"]:
            raise ValueError("Native world observation identity differs")
    return metadata


def _judge_endpoint(value: object) -> dict | None:
    """The host-owned Judge wrapper a fixture dispatch may use; never native configuration."""
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or set(value) != {"upstream", "inspection", "files"}
        or not isinstance(value["upstream"], str)
        or not value["upstream"]
        or not isinstance(value["inspection"], str)
        or not isinstance(value["files"], dict)
        or not all(isinstance(name, str) and isinstance(path, str) for name, path in value["files"].items())
    ):
        raise ValueError("Invalid Judge endpoint")
    return {
        "upstream": value["upstream"],
        "inspection": Path(value["inspection"]),
        "wrapper_files": {name: Path(path) for name, path in value["files"].items()},
    }


def _resolve_judge(options: dict, recorded: dict, request: dict, *, composed: bool) -> None:
    """Bind the derived Judge identity: a frozen one is never overridden and the runtime model never fills in."""
    if request.get("calibration"):
        return
    named, frozen = request.get("judge_model"), recorded.get("judge_model")
    if named is not None and frozen is not None and named != frozen:
        raise ValueError("The recorded Judge model is frozen and cannot be overridden")
    if not composed:
        if named is not None and frozen is None:
            raise ValueError("This task composes no Judge for an explicitly named model")
        return
    if request.get("judgement"):
        answered = fixture_judge.saved_model(Path(request["judgement"]))
        if any(model not in (None, answered) for model in (named, frozen)):
            raise ValueError("The saved Judge identity differs from the requested or recorded Judge")
        named = answered
    model = frozen or named
    if model is not None:
        if options.get("judge_mode", "wrapper") != "wrapper":
            raise ValueError("A fixture Judge dispatches only through the inspected wrapper")
        # Derived options only; the native record and its options digest stay unchanged.
        options.update(judge_mode="wrapper", judge_model=model)


def evaluate_record(task: Path, evaluator: Callable, request: dict, *, judge_factory: Callable | None = None) -> dict:
    """Task-owned composition calls its evaluator with the original reservation rules.

    An explicit `judge_factory` builds the Judge from the one-use reservation callback and the
    stronger fixture receipt contract applies; without one the evaluator receives the callback.
    For a saved judgement the same factory receives `saved` instead and nothing is reserved.
    """
    record, output = Path(request["record"]).resolve(), Path(request["output"]).resolve()
    sources = source_manifest()
    metadata = validate_record(record, task, sources)
    if output.is_relative_to(record / "evidence") or output.exists():
        raise ValueError("Derived native evaluation needs a new directory outside recorded evidence")
    if request.get("judgement") and request.get("dispatch"):
        raise ValueError("Supply saved judgement or dispatch, not both")
    options = {
        **metadata["options"],
        "identity": {"task": task.name, "sources_sha256": digest(sources)},
        "submission": str(record / "evidence/submission.yaml"),
        "evaluation": str(output / "evaluation"),
        "dispatch": request.get("dispatch", False),
    }
    contract = record / "evaluation/task-contract.json"
    if contract.is_file():
        options["contract"] = str(contract)
    if request.get("judgement"):
        options["judgement"] = str(Path(request["judgement"]).resolve())
    if request.get("calibration"):
        if not request.get("judge_model"):
            raise ValueError("Calibration requires an explicit judge model")
        options.update(calibration=request["calibration"], judge_mode="codex", judge_model=request["judge_model"])
    cost = policy(task).get("judge_calls", 0)
    if (options["dispatch"] or request.get("calibration") or request.get("judgement")) and not cost:
        raise ValueError("This task has no semantic judge")
    endpoint = _judge_endpoint(request.get("judge"))
    if endpoint is not None and (judge_factory is None or not options["dispatch"]):
        raise ValueError("A Judge endpoint needs a requested dispatch by a task that composes a fixture Judge")
    _resolve_judge(options, metadata["options"], request, composed=judge_factory is not None)
    consumed = False
    duplicate = False
    work_complete = False
    receipts = output / "judge" if judge_factory is not None and options["dispatch"] else None
    saved = Path(request["judgement"]) if judge_factory is not None and request.get("judgement") else None

    def settle(call: Callable, ledger: Path, index: int, event: dict) -> dict:
        if receipts is None:
            return call()
        fixture_judge.stamp(receipts, ledger, index, event)
        with fixture_judge.unknown_while_unsettled(receipts):
            call()
        # The receipt and its artifacts, not the call's return value, prove what was dispatched.
        return fixture_judge.dispatch_outcome(
            receipts,
            ledger,
            index,
            event,
            model=options.get("judge_model"),
            inspection_sha256=sha256(endpoint["inspection"]) if endpoint else None,
        )

    def reserved(call: Callable) -> dict:
        nonlocal consumed, duplicate, work_complete
        if consumed:
            duplicate = True
            raise ValueError("Native judge reservation was consumed more than once")
        if not cost or source_manifest() != sources:
            raise ValueError("Native judge reservation or source identity differs")
        consumed = True
        existing = request.get("reservation")
        if existing is not None:
            ledger = Ledger.open(Path(existing["ledger"]), ceilings=None, stop_after_failure=None)
            index = existing["index"]
            if type(index) is not int or not 0 <= index < len(ledger.data["events"]):
                raise ValueError("Native judge requires the exact pending host reservation")
            event = ledger.data["events"][index]
            if (
                event != existing["event"]
                or event["phase"] != "judge"
                or event["status"] != "unknown"
                or event["count"] != cost
            ):
                raise ValueError("Native judge requires the exact pending host reservation")
            result = settle(call, Path(existing["ledger"]), index, event)
            work_complete = isinstance(result, dict) and result.get("status") == "complete"
            return result
        series = Path(request["series_dir"]) if request.get("series_dir") else None
        ledger = open_ledger(output, series, {"judge": cost}, False, parse_ceilings(request.get("series_ceiling")))
        with ledger.reserved("judge", f"{output.name}/judge", cost, output / "result.json", cost) as outcome:
            index = len(ledger.data["events"]) - 1
            result = settle(call, ledger.path, index, ledger.data["events"][index])
            work_complete = isinstance(result, dict) and result.get("status") == "complete"
            outcome.passed = work_complete
            return result

    if options["dispatch"] and receipts is None:
        options["reserved_judge"] = reserved
    output.mkdir(parents=True, exist_ok=False)
    if saved is not None:
        assert judge_factory is not None
        (output / "judge").mkdir()
        # A saved Judge replays `saved` exactly and writes only its replay receipt into `directory`.
        replay = judge_factory(
            directory=output / "judge",
            native_identity=(record / "native-task.json").read_bytes(),
            options=dict(options),
            saved=saved,
        )
        returned = evaluator(record / "evidence", options, judge=replay)
        if (
            isinstance(returned, dict)
            and (returned.get("quality") or {}).get("status") == "complete"
            and not (output / "judge/replay-receipt.json").is_file()
        ):
            raise ValueError("Saved Judge quality without a replay")
    elif receipts is None:
        returned = evaluator(record / "evidence", options)
    else:
        assert judge_factory is not None
        judge = judge_factory(
            directory=receipts,
            native_identity=(record / "native-task.json").read_bytes(),
            options=dict(options),
            reserved=reserved,
            wrapper=endpoint or {"upstream": None, "inspection": None, "wrapper_files": None},
        )
        if fixture_judge.receipt_state(receipts) != "not_dispatched":
            raise ValueError("Judge factory must initialize an undispatched fresh receipt")
        with fixture_judge.unknown_while_unsettled(receipts):
            returned = evaluator(record / "evidence", options, judge=judge)
            # Rubric catches may have hidden an unknown dispatch; keep acceptance, never its quality.
            if fixture_judge.receipt_state(receipts) in fixture_judge.UNSETTLED and isinstance(returned, dict):
                write_json(output / "result.json", validate_result({**returned, "quality": None}))
        fixture_judge.skip(receipts, "The evaluator completed no Judge dispatch")
    try:
        result = validate_result(returned)
    except ValueError:
        if options["dispatch"] and isinstance(returned, dict) and returned.get("quality") is not None:
            diagnostic = validate_result({**returned, "quality": None})
            if source_manifest() != sources:
                raise ValueError("Native evaluator sources changed") from None
            write_json(output / "result.json", diagnostic)
        raise
    if source_manifest() != sources:
        raise ValueError("Native evaluator sources changed")
    write_json(output / "result.json", result)
    if options["dispatch"] and (
        not consumed or duplicate or not work_complete or (result["quality"] or {}).get("status") != "complete"
    ):
        raise ValueError("Judge dispatch incomplete: callback and complete quality are required")
    return result


def reevaluate_native(record: Path, output: Path, judgement: Path | None, **options) -> dict:
    """Select only the fixed source-checked native task and invoke its recorded evaluator."""
    metadata = json.loads((record / "native-task.json").read_text())
    task = select_tasks(resource_root() / "tasks", [metadata.get("name")])[0]
    sources = source_manifest()
    validate_record(record, task, sources)
    request = {
        "action": "evaluate",
        "record": str(record.resolve()),
        "output": str(output.resolve()),
        "judgement": str(judgement.resolve()) if judgement else None,
        **options,
    }
    # A task process lost after its Judge start receipt is an unknown spend, not a known failure.
    with fixture_judge.unknown_while_unsettled(output / "judge"):
        result = invoke(task, request, sources)
    return validate_result(result)
