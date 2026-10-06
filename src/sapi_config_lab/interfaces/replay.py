"""Validate the preselected historical artifacts; never generate or repair YAML."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from sapi_config_lab.core.evidence import json_text, sha256
from sapi_config_lab.runtime.agency import strict_json

SCHEMA = "sapi-lab-generated-selection/v1"
SOURCE_REPORT_SHA256 = "9858951f9111990ee244be3a1edaf562304d77c090eb8fbf5a00053f8bb45bc4"
FROZEN_CORE_SHA256 = "94a16bf575be91ad05eae75eb0eb6d0123b02d5e841e29825ab5a876dcda15d3"
# Selected in the approved plan, before observing any live result.
FROZEN = {
    "invoice-total": (
        "invoice-total__83VHojk",
        "2e313b889eb9c4ed6a3d8ea31a06012e0d4ac9e4b9eedd7879304136a9446dbd",
        "206dc3b71681bbd80f92c408feeb453115f48e1fa4f1243289ffe1d98f34cbc7",
    ),
    "ticket-routing": (
        "ticket-routing__HmLivij",
        "a81d2ec0a6f25ef2454985f7f28d2e758d4d6e3fdd83fa821176046e6ba921b0",
        "9f33481ecd394e17b90912f27092ae2c0b5f337aeec2d08074ccdebbbf953200",
    ),
    "competitor-report": (
        "competitor-report__SfEWsWK",
        "495dc552ac531002aa6ccb3e189cc4878aae83561db6fc704c1dba3b49e2070e",
        "3775b80868a30886b9102ffbb6b385248c215cf45321a86b758a3a76bfdbb81f",
    ),
}
PROVENANCE_FILES = ("agent/generation.json", "agent/prompt.txt", "result.json", "verifier/report.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path: Path):
    return strict_json(path.read_bytes())


def selection_manifest(source_report: Path) -> dict:
    """Produce the one permitted selection from the preserved source report."""
    source_report = source_report.resolve()
    require(sha256(source_report) == SOURCE_REPORT_SHA256, "Historical source report hash mismatch")
    core = source_report.parent / "frozen-core.json"
    require(sha256(core) == FROZEN_CORE_SHA256, "Historical source manifest hash mismatch")
    entries = []
    for scenario, (trial, submission_hash, prompt_hash) in FROZEN.items():
        job = source_report.parent / "jobs/generated"
        candidates = sorted(path.name for path in job.glob(scenario + "__*") if path.is_dir())
        require(candidates and candidates[0] == trial, "Frozen selection is not the first historical trial")
        directory = job / trial
        submission = directory / "agent/submission.yaml"
        require(sha256(submission) == submission_hash, "Frozen submission hash mismatch")
        require(sha256(directory / "agent/prompt.txt") == prompt_hash, "Original prompt hash mismatch")
        generation = read_json(directory / "agent/generation.json")
        require(
            generation.get("status") == "submitted"
            and generation.get("generation_calls") == 1
            and generation.get("repairs") == 0
            and generation.get("model") == "gpt-6-astra"
            and generation.get("observed_tool_markers") == []
            and generation.get("submission_sha256") == submission_hash
            and generation.get("prompt_sha256") == prompt_hash,
            "Historical generation provenance mismatch",
        )
        native = read_json(directory / "result.json")
        acceptance = read_json(directory / "verifier/report.json")
        require(
            native.get("task_name") == scenario
            and not native.get("exception_info")
            and (native.get("verifier_result") or {}).get("rewards") == {"reward": 1.0},
            "Historical Harbor acceptance missing",
        )
        require(
            acceptance.get("scenario") == scenario
            and acceptance.get("mode") == "stub"
            and acceptance.get("passed") is True,
            "Historical independent acceptance missing",
        )
        entries.append(
            {
                "scenario": scenario,
                "source_trial": trial,
                "source_yaml_path": str(submission),
                "source_yaml_sha256": submission_hash,
                "provenance": {name: sha256(directory / name) for name in PROVENANCE_FILES},
            }
        )
    return {
        "schema": SCHEMA,
        "experiment_kind": "frozen_generated_replay",
        "source_report": {"path": str(source_report), "sha256": SOURCE_REPORT_SHA256},
        "frozen_core_sha256": FROZEN_CORE_SHA256,
        "entries": entries,
    }


def load_selection(path: Path, *, copy_to: Path | None = None, scenarios: tuple[str, ...] | None = None) -> dict:
    """Recompute provenance; an edited/duplicate/missing entry is never accepted."""
    manifest = read_json(path)
    require(isinstance(manifest, dict), "Selection manifest must be an object")
    source = manifest.get("source_report")
    require(isinstance(source, dict) and isinstance(source.get("path"), str), "Missing historical source report")
    if scenarios is None:
        expected = selection_manifest(Path(source["path"]))
    else:
        require(len(scenarios) == 1, "Expansion replay selects one admitted scenario")
        expected = expansion_selection(Path(source["path"]), scenarios[0])
    require(manifest == expected, "Selection manifest does not match the frozen historical selection")
    submissions = {}
    if copy_to is not None:
        copy_to.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(path, copy_to / "selection.json")
        shutil.copyfile(Path(source["path"]), copy_to / "source-report.json")
        shutil.copyfile(Path(source["path"]).parent / "frozen-core.json", copy_to / "frozen-core.json")
    for entry in manifest["entries"]:
        yaml_path = Path(entry["source_yaml_path"])
        submissions[entry["scenario"]] = {"path": yaml_path, "sha256": entry["source_yaml_sha256"]}
        if "cases_path" in entry:
            submissions[entry["scenario"]].update(
                cases_path=Path(entry["cases_path"]), cases_sha256=entry["cases_sha256"]
            )
        if copy_to is not None:
            destination = copy_to / entry["scenario"]
            destination.mkdir()
            shutil.copyfile(yaml_path, destination / "submission.yaml")
            if "cases_path" in entry:
                shutil.copyfile(entry["cases_path"], destination / "cases.json")
            for name in PROVENANCE_FILES:
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(yaml_path.parent.parent / name, target)
    return submissions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario")
    args = parser.parse_args()
    manifest = (
        expansion_selection(args.source_report, args.scenario)
        if args.scenario
        else selection_manifest(args.source_report)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json_text(manifest, ensure_ascii=True))
    load_selection(args.output, scenarios=(args.scenario,) if args.scenario else None)
    print(
        json.dumps(
            {"selection_manifest": str(args.output), "submissions": len(manifest["entries"]), "generation_calls": 0}
        )
    )


def expansion_selection(source_report: Path, scenario: str) -> dict:
    """Select the first submitted attempt only after both frozen stub attempts pass."""
    from sapi_config_lab.core.provenance import source_manifest
    from sapi_config_lab.core.scenarios import EXPANSION_SCENARIOS, EXTENSION_SCENARIOS

    require(scenario in {**EXPANSION_SCENARIOS, **EXTENSION_SCENARIOS}, "Unknown bounded selection")
    refinement = scenario in EXTENSION_SCENARIOS
    attempts = 3 if refinement else 2
    source_report = source_report.resolve()
    report = read_json(source_report)
    frozen = source_report.parent / "frozen-core.json"
    require(read_json(frozen) == source_manifest(), "Expansion source inventory changed")
    require(
        report.get("schema") == "sapi-lab-generation/v1"
        and report.get("status") == "passed"
        and report.get("frozen_core_unchanged") is True
        and report.get("attempts_per_task") == attempts,
        "Selection requires all frozen authoring attempts accepted",
    )
    trials = report.get("trials", [])
    require(
        len(trials) == attempts
        and all(row.get("scenario") == scenario and row.get("passed") is True for row in trials),
        "Selection must contain exactly its successful authoring cohort",
    )
    candidates = []
    for row in trials:
        path = Path(row["result_path"]).resolve()
        require(path.is_relative_to(source_report.parent / "jobs/generated"), "Trial outside generation evidence")
        native = read_json(path)
        directory = path.parent
        generation = read_json(directory / "agent/generation.json")
        acceptance = read_json(directory / "verifier/report.json")
        submission = directory / "agent/submission.yaml"
        prompt = directory / "agent/prompt.txt"
        require(
            native.get("task_name") == scenario
            and not native.get("exception_info")
            and (native.get("verifier_result") or {}).get("rewards") == {"reward": 1.0},
            "Native stub trial failed",
        )
        require(
            generation.get("status") == "submitted"
            and generation.get("generation_calls") == 1
            and generation.get("repairs") == 0
            and generation.get("model") == "gpt-6-astra"
            and generation.get("observed_tool_markers") == []
            and generation.get("submission_sha256") == sha256(submission)
            and generation.get("prompt_sha256") == sha256(prompt),
            "Expansion authoring provenance mismatch",
        )
        require(
            acceptance.get("scenario") == scenario
            and acceptance.get("mode") == "stub"
            and acceptance.get("passed") is True
            and acceptance.get("submission_sha256") == sha256(submission),
            "Expansion stub acceptance mismatch",
        )
        require(sha256(prompt) == report.get("prompt_sha256", {}).get(scenario), "Expansion prompt drift")
        started = native.get("agent_execution", {}).get("started_at")
        require(isinstance(started, str) and started, "Missing authoring attempt order")
        candidates.append((started, directory.name, submission))
    require(len({row[0] for row in candidates}) == attempts, "Ambiguous authoring attempt order")
    _, trial, submission = min(candidates)
    case_path = source_report.parent / "task-packages" / scenario / "tests/cases.json"
    require(set(read_json(case_path)) == {scenario}, "Expansion private fixture set mismatch")
    require(
        report.get("private_cases_sha256", {}).get(scenario) == sha256(case_path),
        "Expansion private fixture hash mismatch",
    )
    return {
        "schema": "sapi-lab-refinement-selection/v1" if refinement else "sapi-lab-expansion-selection/v1",
        "experiment_kind": "bounded_refinement" if refinement else "bounded_expansion",
        "source_report": {"path": str(source_report), "sha256": sha256(source_report)},
        "frozen_core_sha256": sha256(frozen),
        "entries": [
            {
                "scenario": scenario,
                "source_trial": trial,
                "source_yaml_path": str(submission),
                "source_yaml_sha256": sha256(submission),
                "cases_path": str(case_path),
                "cases_sha256": sha256(case_path),
                "provenance": {name: sha256(submission.parent.parent / name) for name in PROVENANCE_FILES},
            }
        ],
    }


if __name__ == "__main__":
    main()
