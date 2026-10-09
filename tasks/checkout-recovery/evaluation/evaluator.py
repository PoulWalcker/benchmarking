"""Score recorded checkout evidence with the frozen judge and independent upstream rubric."""

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.pinned_source import PinnedSource, read_manifest

from .calibration import calibration_fixture, compare_calibration
from .scoring import evaluate_once, freeze_contract, recorded_run_log

ROOT = Path(__file__).resolve().parent.parent


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("Saved evaluation inputs require JSON values")
    return dict(value)


def _document(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, (str, Path)):
        result = json.loads(Path(value).read_text())
    else:
        result = json.loads(json.dumps(value, default=_mapping, allow_nan=False))
    if not isinstance(result, dict):
        raise ValueError("Saved evaluation input must be a JSON object")
    return result


def evaluate(evidence: Path, options: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read one frozen trial; only the explicit demo judge may dispatch locally."""
    evidence = Path(evidence).resolve()
    output = Path(options["evaluation"]).resolve()
    if output.is_relative_to(evidence):
        raise ValueError("Derived evaluation must be outside original evidence")
    saved = _document(options["contract"]) if options.get("contract") is not None else None
    judge: Mapping[str, Any] = saved["judge"] if saved else {"mode": "demo", "model": None}
    manifest = Path(options.get("source_manifest", ROOT / "provenance/autowfbench-source.json"))
    source_root = Path(options.get("source_root", ROOT / "vendor/autowfbench"))
    if "source_root" not in options and not source_root.is_dir():
        source_root = ROOT.parent.parent / ".cache/autowfbench" / read_manifest(manifest)["revision"]
    if saved is not None:
        original = freeze_contract(
            PinnedSource(manifest, source_root),
            "production-checkout-recovery",
            judge_mode=judge["mode"],
            judge_model=judge["model"],
            artifact=OutputArtifact("incident_summary", "incident-summary.md"),
        )
        if original.as_dict() != saved:
            raise ValueError("Saved task contract differs from frozen source/package/judge")
    contract = freeze_contract(
        PinnedSource(manifest, source_root),
        "production-checkout-recovery",
        judge_mode=options.get("judge_mode", judge["mode"]),
        judge_model=options.get("judge_model", judge["model"]),
        artifact=OutputArtifact("incident_summary", "incident-summary.md"),
    )
    if saved is not None and not options.get("calibration") and contract.as_dict() != saved:
        raise ValueError("Saved task contract differs from frozen source/package/judge")
    reply = _document(options["judgement"]) if options.get("judgement") is not None else None
    dispatch = options.get("dispatch", reply is None and contract.judge_mode == "demo")
    if type(dispatch) is not bool:
        raise ValueError("Judge dispatch must be boolean")
    reserve = options.get("reserved_judge")
    if dispatch and contract.judge_mode != "demo" and not callable(reserve):
        raise ValueError("Paid judge dispatch requires the host reservation path")
    run_log = recorded_run_log(contract, evidence)
    fixture = None
    if options.get("calibration"):
        fixture = calibration_fixture(contract, run_log, options["calibration"])
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
        run_log = fixture["run_log"]

    def score():
        return evaluate_once(contract, run_log, output, judgement=reply, dispatch=dispatch)

    if dispatch and contract.judge_mode != "demo":
        assert callable(reserve)
        report = reserve(score)
    else:
        report = score()
    if fixture is not None:
        (output.parent / "comparison.json").write_text(
            json.dumps(compare_calibration(fixture, report), indent=2) + "\n"
        )
    result = {
        "execution": run_log["termination_reason"] == "completed",
        "acceptance": report.get("execution_pass") is True,
        "quality": {key: report[key] for key in ("status", "score_0_10", "normalized_reward")},
    }
    trial = json.loads((evidence / "trial.json").read_text())
    acceptance = {
        "schema": "sapi-lab-upstream-acceptance/v1",
        "scenario": "checkout-recovery",
        "mode": trial["llm_mode"],
        "submission_sha256": trial["submission_sha256"],
        "passed": result["acceptance"],
        "result": result,
        "native_execution": trial.get("native_execution"),
        "terminal_completion": trial.get("terminal_completion"),
    }
    (output / "report.json").write_text(json.dumps(acceptance, indent=2, ensure_ascii=False) + "\n")
    return {**result, "harbor_reward": report["normalized_reward"]}
