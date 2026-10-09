"""Read recorded trial facts and legacy layouts without importing execution or evaluator dispatch."""

from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any

# Recorded trials carry this schema, so its pre-provider name stays.
HOSTED_REPORT = "sapi-lab-upstream-acceptance/v1"
ADMISSION_REPORT = "sapi-lab-admission/v1"
NOT_EVALUATED: dict[str, Any] = {"execution": None, "acceptance": None, "quality": None}


def validate_result(result: Any) -> dict:
    """Validate the normalized boundary without imposing any benchmark's scoring semantics."""
    if not isinstance(result, dict) or not {"execution", "acceptance", "quality"} <= result.keys():
        raise ValueError("Evaluator result requires execution, acceptance and quality")
    for key in ("execution", "acceptance"):
        if result[key] is not None and type(result[key]) is not bool:
            raise ValueError(f"Evaluator result {key} must be boolean or null")
    reward = result.get("harbor_reward")
    if reward is not None and (type(reward) not in (int, float) or not math.isfinite(reward) or not 0 <= reward <= 1):
        raise ValueError("Evaluator harbor_reward must be finite in [0, 1] or null")
    value = result["quality"]
    if value is None:
        return result
    if not isinstance(value, dict) or not {"status", "score_0_10", "normalized_reward"} <= value.keys():
        raise ValueError("Evaluator quality requires status, score_0_10 and normalized_reward")
    if not isinstance(value["status"], str) or not value["status"]:
        raise ValueError("Evaluator quality status must be a nonempty string")
    for key, maximum in (("score_0_10", 10), ("normalized_reward", 1)):
        number = value[key]
        if value["status"] == "complete":
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= maximum:
                raise ValueError(f"Complete evaluator quality requires a finite {key} in [0, {maximum}]")
        elif number is not None:
            raise ValueError("Unscored evaluator quality must retain null scores")
    return result


def quality(evaluation: dict | None) -> dict | None:
    if evaluation is None:
        return None
    return {key: evaluation.get(key) for key in ("status", "score_0_10", "normalized_reward")}


def verifier_result(report: dict | None, rubric: dict | None) -> dict:
    if not report:
        return dict(NOT_EVALUATED)
    executed = [
        row.get("execution", {}).get("succeeded") for row in report.get("cases", []) if row.get("kind") == "positive"
    ]
    return {
        "execution": all(value is True for value in executed) if executed else None,
        "acceptance": report.get("passed"),
        "quality": quality(rubric),
    }


def trial_result(trial: dict, *, root: Path | None = None) -> dict:
    acceptance = trial.get("acceptance") or {}
    path = None
    if "verdict_path" in trial:
        path = recorded_path(trial["verdict_path"], root)
    elif "result_path" in trial:
        record = (
            recorded_path(trial["evaluation_path"], root).parent.parent
            if trial.get("evaluation_path")
            else recorded_path(trial["result_path"], root).parent / "verifier"
        )
        path = record / "result.json"
    if path is not None:
        if path.exists():
            result = validate_result(json.loads(path.read_text()))
            if acceptance.get("schema") == ADMISSION_REPORT and any(result[key] is not None for key in NOT_EVALUATED):
                raise ValueError("Admission cannot carry evaluated verdict facts")
            return result
        if (
            "verdict_path" in trial
            or (path.parent / "benchmark.json").exists()
            or (path.parent / "native-task.json").exists()
        ):
            return dict(NOT_EVALUATED)
    if acceptance.get("schema") == HOSTED_REPORT:
        return acceptance["result"]
    if acceptance.get("schema") == ADMISSION_REPORT:
        return dict(NOT_EVALUATED)
    # Historical summaries retain facts even when the original trial files are unavailable.
    if trial.get("result") is not None:
        return validate_result(trial["result"])
    rubric = recorded_path(trial["result_path"], root).parent / "verifier/evaluation/evaluation.json"
    return verifier_result(acceptance, json.loads(rubric.read_text()) if rubric.exists() else None)


def trial_accepted(trial: dict) -> bool:
    """No Harbor exception, and the scenario's evaluator accepted the trial."""
    acceptance = trial.get("acceptance") or {}
    if trial["exception"]:
        return False
    if acceptance.get("schema") == ADMISSION_REPORT:
        return False
    if "verdict_path" in trial:
        return validate_result(trial["result"])["acceptance"] is True
    if acceptance.get("schema") == HOSTED_REPORT:
        return acceptance["result"]["acceptance"] is True
    return trial["rewards"] == {"reward": 1.0} and acceptance.get("passed") is True


def trial_seconds(trial: dict) -> int | None:
    """Wall-clock seconds Harbor recorded for one trial, if it recorded both ends."""
    path = Path(trial["result_path"])
    if not path.exists():
        return None
    recorded = json.loads(path.read_text())
    try:
        started, finished = (datetime.fromisoformat(recorded[key]) for key in ("started_at", "finished_at"))
    except KeyError, TypeError, ValueError:
        return None
    return round((finished - started).total_seconds())


def load_trials(job: Path, hosted: dict[str, Path] | None = None) -> list[dict]:
    """One row per Harbor trial; a hosted trial's report is its host's record, never the container's copy."""
    trials = []
    for directory in sorted(path for path in job.glob("*") if path.is_dir()):
        path = directory / "result.json"
        trial = json.loads(path.read_text()) if path.exists() else {}
        config = directory / "config.json"
        task = json.loads(config.read_text()).get("task", {}) if config.exists() else {}
        name = trial.get("task_name") or (Path(task["path"]).name if task.get("path") else None)
        record = (hosted or {}).get(name, path.parent / "verifier") if name else path.parent / "verifier"
        report_path = record / "evaluation/report.json"
        row: dict[str, Any] = {
            "task_name": name,
            "trial_id": trial.get("id") or directory.name,
            "trial_path": str(directory.relative_to(job.parent.parent)),
            "evidence_path": str(directory.relative_to(job.parent.parent) / "verifier"),
            "partial": not path.exists(),
            "rewards": (trial.get("verifier_result") or {}).get("rewards"),
            "exception": trial.get("exception_info"),
            "result_path": str(path),
            "evaluation_path": str(report_path),
            "acceptance": json.loads(report_path.read_text()) if report_path.exists() else None,
        }
        if (
            (record / "result.json").exists()
            or (record / "benchmark.json").exists()
            or (record / "native-task.json").exists()
        ):
            row["verdict_path"] = str(record / "result.json")
        if (record / "native-task.json").exists():
            row["native_task"] = json.loads((record / "native-task.json").read_text())
        if (record / "benchmark.json").exists():
            row["benchmark"] = json.loads((record / "benchmark.json").read_text())
        trials.append(
            {
                **row,
                "result": trial_result(row),
                "native_execution": (row["acceptance"] or {}).get("native_execution"),
                "terminal_completion": (row["acceptance"] or {}).get("terminal_completion"),
            }
        )
    return trials


def recorded_path(value: str, root: Path | None) -> Path:
    """Resolve relocated run references without changing the saved path or consulting active tasks."""
    path = Path(value)
    if root is None:
        return path
    root = root.absolute()
    if path.is_absolute():
        if path.is_relative_to(root):
            return path
        for marker in ("jobs", "environments"):
            if marker in path.parts:
                path = Path(*path.parts[path.parts.index(marker) :])
                break
        else:
            raise ValueError("Historical absolute reference has no recorded run layout: " + value)
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("Historical reference escapes the selected run: " + value)
    return root / path


def read_report(path: Path, *, root: Path | None = None) -> dict:
    """Keep the recorded document and expose trial facts beside it, including unavailable observations."""
    document = json.loads(path.read_text())
    trials = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("task_name") and "result_path" in value:
                acceptance = value.get("acceptance") or {}
                trials.append(
                    {
                        **value,
                        "result": (
                            trial_result(value, root=root)
                            if "verdict_path" in value or acceptance.get("schema") == ADMISSION_REPORT
                            else value.get("result") or trial_result(value, root=root)
                        ),
                        "native_execution": acceptance.get("native_execution"),
                        "terminal_completion": acceptance.get("terminal_completion"),
                    }
                )
            else:
                for item in value.values():
                    visit(item)

    visit(document)
    return {"recorded": document, "trials": trials}
