"""Select recorded generated submissions for replay; never generate or repair YAML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from sapi_config_lab.coordinate.packages import selected_cases
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.scenarios import SCENARIOS
from sapi_config_lab.evidence import json_text, sha256
from sapi_config_lab.execute.agency import strict_json

SCHEMA = "sapi-lab-selection/v1"
RULE = "first-started-attempt"
PROVENANCE_FILES = ("agent/generation.json", "agent/prompt.txt", "result.json", "verifier/evaluation/report.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path: Path):
    return strict_json(path.read_bytes())


def authored_once(generation: dict, submission_sha256: str, prompt_sha256: str) -> bool:
    """One wrapper call, no repair, no tools, and the exact recorded prompt and answer."""
    return (
        generation.get("status") == "submitted"
        and generation.get("generation_calls") == 1
        and generation.get("repairs") == 0
        and isinstance(generation.get("model"), str)
        and generation.get("observed_tool_markers") == []
        and generation.get("submission_sha256") == submission_sha256
        and generation.get("prompt_sha256") == prompt_sha256
    )


def select_submission(source_report: Path, scenarios: tuple[str, ...]) -> dict:
    """The one permitted selection from a generation run, by RULE."""
    source_report = source_report.resolve()
    root = source_report.parent
    report = read_json(source_report)
    require(
        report.get("schema") == "sapi-lab-generation/v2" and report.get("source_unchanged") is True,
        "Selection needs a generation report whose sources stayed unchanged",
    )
    frozen = root / "source-manifest.json"
    require(read_json(frozen) == source_manifest(), "Sources changed since the submissions were generated")
    require(scenarios and len(set(scenarios)) == len(scenarios), "Select each scenario once")
    entries = []
    for scenario in scenarios:
        attempts = []
        for row in report.get("trials", []):
            if row.get("scenario") != scenario:
                continue
            path = Path(row["result_path"]).resolve()
            require(path.is_relative_to(root / "jobs"), "Trial outside the generation run")
            started = read_json(path).get("agent_execution", {}).get("started_at")
            require(isinstance(started, str) and started, "Missing authoring attempt order")
            attempts.append({"trial": path.parent.name, "started_at": started, "passed": row.get("passed") is True})
        require(attempts, "No recorded attempt for " + scenario)
        require(len({row["started_at"] for row in attempts}) == len(attempts), "Ambiguous authoring attempt order")
        attempts.sort(key=lambda row: row["started_at"])
        first = attempts[0]
        directory = next(
            Path(r["result_path"]).parent
            for r in report["trials"]
            if Path(r["result_path"]).parent.name == first["trial"]
        )
        submission, prompt = directory / "agent/submission.yaml", directory / "agent/prompt.txt"
        native = read_json(directory / "result.json")
        acceptance = read_json(directory / "verifier/evaluation/report.json")
        require(
            first["passed"]
            and native.get("task_name") == scenario
            and not native.get("exception_info")
            and (native.get("verifier_result") or {}).get("rewards") == {"reward": 1.0}
            and acceptance.get("scenario") == scenario
            and acceptance.get("mode") == "stub"
            and acceptance.get("passed") is True
            and acceptance.get("submission_sha256") == sha256(submission),
            "The first attempt did not pass its stub gate; no later attempt is selected in its place",
        )
        require(
            authored_once(read_json(directory / "agent/generation.json"), sha256(submission), sha256(prompt)),
            "Authoring provenance mismatch",
        )
        require(sha256(prompt) == report.get("prompt_sha256", {}).get(scenario), "Prompt drift")
        entry = {
            "scenario": scenario,
            "source_trial": first["trial"],
            "source_yaml_path": str(submission),
            "source_yaml_sha256": sha256(submission),
        }
        cases = root / "task-packages" / scenario / "tests/cases.json"
        if selected_cases(SCENARIOS[scenario]) is not None:
            require(set(read_json(cases)) == {scenario}, "Private fixture set mismatch")
            require(
                report.get("private_cases_sha256", {}).get(scenario) == sha256(cases), "Private fixture hash mismatch"
            )
            entry.update(cases_path=str(cases), cases_sha256=sha256(cases))
        else:
            require(not cases.exists(), "Fixtures staged but not recorded")
        entries.append(
            {
                **entry,
                "attempts": attempts,
                "provenance": {name: sha256(directory / name) for name in PROVENANCE_FILES},
            }
        )
    return {
        "schema": SCHEMA,
        "rule": RULE,
        "source_report": {"path": str(source_report), "sha256": sha256(source_report)},
        "source_manifest_sha256": sha256(frozen),
        "entries": entries,
    }


def load_selection(path: Path, *, copy_to: Path | None = None) -> dict:
    """Recompute the selection; return {scenario: {path, sha256[, cases, cases_sha256]}}."""
    manifest = read_json(path)
    require(isinstance(manifest, dict) and manifest.get("schema") == SCHEMA, "Unknown selection manifest")
    source = Path(manifest["source_report"]["path"])
    scenarios = tuple(entry["scenario"] for entry in manifest.get("entries", []))
    require(manifest == select_submission(source, scenarios), "Selection manifest does not match its source report")
    if copy_to is not None:
        copy_to.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(path, copy_to / "selection.json")
        shutil.copyfile(source, copy_to / "source-report.json")
    submissions = {}
    for entry in manifest["entries"]:
        yaml_path = Path(entry["source_yaml_path"])
        submission: dict = {"path": yaml_path, "sha256": entry["source_yaml_sha256"]}
        if "cases_path" in entry:
            cases_path = Path(entry["cases_path"])
            submission.update(
                cases=json.loads(cases_path.read_text())[entry["scenario"]], cases_sha256=entry["cases_sha256"]
            )
        submissions[entry["scenario"]] = submission
        if copy_to is not None:
            destination = copy_to / entry["scenario"]
            destination.mkdir()
            shutil.copyfile(yaml_path, destination / "submission.yaml")
            if "cases_path" in entry:
                shutil.copyfile(cases_path, destination / "cases.json")
            for name in PROVENANCE_FILES:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(yaml_path.parent.parent / name, target)
    return submissions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-report", type=Path, required=True, help="A generation run's report.json")
    parser.add_argument("--scenario", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = select_submission(args.source_report, tuple(args.scenario))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json_text(manifest, ensure_ascii=True))
    load_selection(args.output)
    print(json.dumps({"selection_manifest": str(args.output), "submissions": len(manifest["entries"]), "rule": RULE}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
