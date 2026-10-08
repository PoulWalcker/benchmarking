#!/usr/bin/env python3
"""Independent task acceptance; it imports no compiler or operation code and never executes anything.

`plan` states which definitions the trusted step must run; `evaluate` checks the recorded
evidence is exactly that plan's, then judges it, writing decisions beside, never into, the evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING or __package__:
    from .contracts import Recorded, Rejected, require, scenario_file
    from .extensions import refinement_corruptions, refinement_model_calls, verify_refinement_task
    from .fixture import FixtureEvaluator
    from .n8n_provenance import check_operation_order, check_provenance, check_rejection, observe_execution, rows
    from .roles import bind_roles, contract_for
    from .rubric import SCHEMA, Judge
    from .rubric_facts import NOT_EVALUATED, observe
    from .rubric_facts import evaluate as score_rubric
else:  # Harbor executes its copied verifier directly.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from contracts import Recorded, Rejected, require, scenario_file
    from extensions import refinement_corruptions, refinement_model_calls, verify_refinement_task
    from fixture import FixtureEvaluator
    from n8n_provenance import check_operation_order, check_provenance, check_rejection, observe_execution, rows
    from roles import bind_roles, contract_for
    from rubric import SCHEMA, Judge
    from rubric_facts import NOT_EVALUATED, observe
    from rubric_facts import evaluate as score_rubric

PLAN_SCHEMA = "sapi-lab-observation-plan/v1"
# Scenario-owned evaluation data under benchmarks/NN-name/evaluation/, packaged beside this verifier.
EVALUATION_FILES = ("contract.json", "rubric.json")
OBSERVATION_SCHEMA = "sapi-lab-observation/v1"
# Corrupted-artifact probe: n8n succeeds and only independent acceptance can notice the wrong result.
WRONG_RESULT = "wrong-result"


def evaluator_for(scenario: str) -> FixtureEvaluator:
    if TYPE_CHECKING or __package__:
        from .fixture_evaluators import evaluator_for as legacy
    else:
        from fixture_evaluators import evaluator_for as legacy
    return legacy(scenario)


def fresh_cases(scenario: str, cases: dict) -> dict:
    fresh = evaluator_for(scenario).fresh
    require(fresh is not None, "This fixture evaluator declares no freshness contract")
    assert fresh is not None
    return fresh(cases)


def check_execution(
    scenario: str,
    inputs: dict,
    run: dict,
    mode: str = "stub",
    *,
    config: dict | None = None,
    case: dict | None = None,
    rubric_runs: list | None = None,
    fixture: FixtureEvaluator | None = None,
) -> dict:
    """Business acceptance and engine provenance must both pass; `rubric_runs` collects facts before acceptance."""
    fixture = evaluator_for(scenario) if fixture is None else fixture
    if fixture.procedure == "refinement":
        return verify_refinement_task(config, run, mode=mode, case=case)
    observation, records = observe_execution(scenario, inputs, run, mode, config=config, contract=fixture.contract)
    if rubric_runs is not None:
        rubric_runs.append(observe(scenario, inputs, observation, case=case, evaluator=fixture))
    require(fixture.business is not None, "This fixture uses a dedicated evaluation procedure")
    assert fixture.business is not None
    fixture.business(inputs, observation, mode, case=case)
    result = {"output_verified": True, "operation_count": len(observation.events), "llm_mode": mode}
    check_operation_order(scenario, inputs, records, observation.roles, fixture.contract)
    return {**result, "n8n_node_count": len(run["run_data"]), "engine_provenance_verified": True}


def expected_model_calls(scenario: str, config: dict, inputs: dict) -> dict[str, str]:
    """Every model call this submission may make for these inputs, by occurrence."""
    revision = config["workflow"]["revision"]
    evaluator = evaluator_for(scenario)
    if evaluator.procedure == "refinement":
        return refinement_model_calls(config)
    contract, binding = contract_for(scenario), bind_roles(scenario, config)
    calls = {}
    for role, obligation in contract["roles"].items():
        if obligation["kind"] != "LLM":
            continue
        when = obligation.get("when")
        if when is not None:
            require(evaluator.guard is not None, "No stated expectation decides this model call: " + role)
            assert evaluator.guard is not None
            if not evaluator.guard(inputs, when):
                continue
        calls[f"{scenario}/r{revision}/{binding[role]}"] = obligation["operation"]
    return calls


def execution_summary(run: dict) -> dict:
    return {
        "execution": run.get("execution", {"status": run.get("status"), "succeeded": run.get("status") == "success"}),
        "input": run.get("input"),
        "llm": run.get("llm"),
    }


def corruption_checks(
    scenario: str,
    inputs: dict,
    run: dict,
    mode: str,
    *,
    config: dict | None = None,
    case: dict | None = None,
    fixture: FixtureEvaluator | None = None,
) -> list[str]:
    """Deliberately corrupt results to test that acceptance cannot be vacuous."""
    evaluator = evaluator_for(scenario) if fixture is None else fixture

    mutations = [("wrong-final-output", evaluator.corrupt_output)]

    def absent_execution(candidate: dict) -> None:
        candidate["run_data"].pop("Result")

    mutations.append(("output-without-real-result-node", absent_execution))

    def false_status(candidate: dict) -> None:
        final = rows(candidate["run_data"]["Result"][0])[0]
        final["trace"][0]["status"] = "skipped"

    mutations.append(("falsified-step-trace", false_status))
    mutations.extend(evaluator.extra_corruptions)
    accepted = []
    for name, mutate in mutations:
        candidate = copy.deepcopy(run)
        mutate(candidate)
        try:
            check_execution(scenario, inputs, candidate, mode, config=config, case=case, fixture=evaluator)
        except Rejected, KeyError, TypeError, IndexError:
            accepted.append(name)
        else:
            raise Rejected("Verifier accepted deliberate corruption: " + name)
    return accepted


def invalid_configs(config: dict):
    bad = copy.deepcopy(config)
    bad["workflow"]["steps"][0]["uses"] = "unregistered.must_be_rejected"
    yield "unknown-operation", bad
    bad = copy.deepcopy(config)
    edges = bad["workflow"].setdefault("dependencies", [])
    if edges:
        first, second = edges[0]
        edges.append([second, first])
    else:
        ids = [step["id"] for step in bad["workflow"]["steps"]]
        edges.extend([[ids[0], ids[-1]], [ids[-1], ids[0]]] if len(ids) > 1 else [[ids[0], ids[0]]])
    for step in bad["workflow"]["steps"]:
        if sum(target == step["id"] for _, target in edges) > 1:
            step["join"] = "all_terminal"
    yield "cycle", bad
    bad = copy.deepcopy(config)
    bad["execution"]["concurrency"] = "required_parallel"
    yield "unsupported-required-parallel", bad


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader: UniqueLoader, node, deep=False):
    output = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in output:
            raise Rejected("Duplicate YAML key: " + str(key))
        output[key] = loader.construct_object(value_node, deep=deep)
    return output


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return sha256_bytes(canonical(value).encode())


def evaluator_identity(scenario: str) -> dict:
    """Which verifier judged: the hash of every module and of the scenario's own evaluation files."""
    here = Path(__file__).resolve().parent
    sources = {path.name: sha256_bytes(path.read_bytes()) for path in sorted(here.glob("*.py"))}
    for name in EVALUATION_FILES:
        path = scenario_file(scenario, name)
        if path is not None:
            sources["evaluation/" + name] = sha256_bytes(path.read_bytes())
    return {"name": "sapi-lab-independent-verifier", "sources_sha256": sha256_bytes(canonical(sources).encode())}


def read_submission(config_path: Path) -> tuple[bytes, dict]:
    raw = config_path.read_bytes()
    expected = os.environ.get("SAPI_EXPECTED_SUBMISSION_SHA256")
    require(not expected or sha256_bytes(raw) == expected, "Container submission hash mismatch")
    config = yaml.load(raw, Loader=UniqueLoader)
    require(isinstance(config, dict), "Submission must be a YAML object")
    return raw, config


def plan(
    scenario: str,
    config_path: Path,
    cases: dict | None,
    mode: str = "stub",
    selected_case: str | None = None,
    deadline_budget: int | None = None,
    fixture: FixtureEvaluator | None = None,
) -> dict:
    """Every definition the trusted step must run: fixture-applied submissions, corruptions and invalid definitions."""
    require(isinstance(cases, dict), "Unknown acceptance scenario")
    assert cases is not None
    raw, config = read_submission(config_path)
    if deadline_budget is not None:
        # The package's verifier timeout was sized for this deadline; a longer one could outlive it.
        execution = config.get("execution")
        deadline = execution.get("deadline_seconds") if isinstance(execution, dict) else None
        require(
            type(deadline) is int and 0 < deadline <= deadline_budget,
            f"Workflow deadline {deadline!r} exceeds the {deadline_budget}s this task was sized for",
            "deadline_exceeds_budget",
        )
    fixture = evaluator_for(scenario) if fixture is None else fixture
    if fixture.procedure == "lifecycle":
        if TYPE_CHECKING or __package__:
            from .lifecycle_submission import plan_lifecycle
        else:
            from lifecycle_submission import plan_lifecycle
        require(mode == "stub", "Live lifecycle requires the bounded lifecycle experiment driver")
        entries = plan_lifecycle(config, cases)
    else:
        entries = []
        for kind in ("positive", "negative"):
            if mode == "live" and kind == "negative":
                continue  # live schema failures are a separate bridge test, not this stub diagnostic contract
            for case in cases[kind]:
                if mode == "live" and cases.get("live_cases") and case["name"] not in cases["live_cases"]:
                    continue
                if selected_case and case["name"] != selected_case:
                    continue
                candidate = copy.deepcopy(config)
                candidate["workflow"]["inputs"] = case["inputs"]
                if mode == "live":
                    candidate["execution"]["deadline_seconds"] = 600
                entries.append({"name": case["name"], "kind": kind, "procedure": "case", "config": candidate})
        if mode == "stub" and not selected_case:
            candidate = copy.deepcopy(config)
            candidate["workflow"]["inputs"] = cases["positive"][0]["inputs"]
            entries.append(
                {
                    "name": "mutated-generated-result",
                    "kind": "mutated-generated-workflow",
                    "procedure": "case",
                    "config": candidate,
                    "artifact_transform": WRONG_RESULT,
                }
            )
            for name, bad in invalid_configs(config):
                entries.append(
                    {"name": "invalid-yaml-" + name, "kind": "invalid-definition", "procedure": "case", "config": bad}
                )
    require(bool(entries), "No test cases selected")
    return {
        "schema": PLAN_SCHEMA,
        "scenario": scenario,
        "mode": mode,
        "submission_sha256": sha256_bytes(raw),
        "fixture_sha256": sha256_bytes(canonical(cases).encode()),
        "entries": entries,
    }


# Native artifacts each recorded execution status requires beside case.json and config.json.
NATIVE_ARTIFACTS = {
    "compile_error": set(),
    "engine_error": set(),
    "import_error": {"workflow.json", "mapping.json", "import.log"},
    "error": {"workflow.json", "mapping.json", "import.log", "execution.stdout.log", "execution.stderr.log"},
    "success": {
        "workflow.json",
        "mapping.json",
        "import.log",
        "execution.stdout.log",
        "execution.stderr.log",
        "execution.json",
        "execution.persisted.json",
        "execution.metadata.json",
    },
}


def inventory(directory: Path) -> dict[str, str]:
    """Every file under a directory, by relative path; links are never evidence."""
    paths = sorted(directory.rglob("*"))
    require(not any(path.is_symlink() for path in paths), "Recorded evidence contains a link")
    return {path.relative_to(directory).as_posix(): sha256_bytes(path.read_bytes()) for path in paths if path.is_file()}


def check_case_record(directory: Path, files: dict[str, str], name: str) -> None:
    """The native artifacts a recorded case must carry, and their agreement with case.json."""
    run = json.loads((directory / "case.json").read_text())
    status = run.get("status")
    require(status in NATIVE_ARTIFACTS, "Unknown recorded execution status: " + name)
    missing = NATIVE_ARTIFACTS[status] - set(files)
    require(not missing, f"Recorded {status} case lacks native artifacts {sorted(missing)}: {name}")
    engine = (run.get("evidence") or {}).get("engine") or {}
    named = {value for key, value in engine.items() if key.endswith("_artifact") and value is not None}
    require(named <= set(files), "Recorded case names a native artifact it did not record: " + name)

    def native(file: str):
        return json.loads((directory / file).read_text()) if file in files else None

    if "workflow.json" in files:
        require(
            files["workflow.json"] == run.get("workflow_sha256"), "Recorded workflow differs from its hash: " + name
        )
    if "mapping.json" in files:
        require(native("mapping.json") == run.get("mapping"), "Recorded mapping differs from the case: " + name)
    persisted, execution, metadata = (
        native("execution.persisted.json"),
        native("execution.json"),
        native("execution.metadata.json"),
    )
    if persisted is not None:
        require(
            persisted.get("resultData", {}).get("runData") == run.get("run_data"),
            "Persisted native record differs from the extracted run data: " + name,
        )
    if execution is not None:
        require(
            execution.get("data", {}).get("resultData", {}).get("runData") == run.get("run_data"),
            "Native execution record differs from the extracted run data: " + name,
        )
    if metadata is not None:
        require(
            str(metadata.get("id")) == run.get("execution_id") and metadata.get("workflowId") == run.get("workflow_id"),
            "Persisted execution identity differs from the case: " + name,
        )


def read_evidence(evidence: Path, expected_plan: dict) -> dict[str, dict[str, str]]:
    """Every recorded file, after checking the record is exactly this plan's."""
    manifest_path = evidence / "observation.json"
    require(manifest_path.is_file(), "No recorded observation; the execution step did not complete")
    manifest = json.loads(manifest_path.read_text())
    require(manifest.get("schema") == OBSERVATION_SCHEMA, "Unknown observation schema")
    require(
        manifest.get("plan_sha256") == digest(expected_plan),
        "Recorded observation is not the plan this verifier issued",
    )
    require(
        (evidence / "submission.yaml").is_file()
        and sha256_bytes((evidence / "submission.yaml").read_bytes()) == expected_plan["submission_sha256"],
        "Recorded submission differs from the evaluated submission",
    )
    recorded = manifest.get("entries")
    require(
        isinstance(recorded, list)
        and [row.get("name") for row in recorded] == [e["name"] for e in expected_plan["entries"]],
        "Recorded observation is incomplete or out of order",
    )
    expected_files = {"submission.yaml", "observation.json"}
    files: dict[str, dict[str, str]] = {}
    for entry, row in zip(expected_plan["entries"], recorded, strict=True):
        directory = evidence / "cases" / entry["name"]
        listed = row.get("files")
        required = {"case.json", "config.json"} if entry["procedure"] == "case" else {"snapshot.json", "event.json"}
        require(isinstance(listed, dict) and required <= set(listed), "Recorded case is incomplete: " + entry["name"])
        on_disk = inventory(directory) if directory.is_dir() else {}
        for name in set(listed) | set(on_disk):
            require(
                listed.get(name) == on_disk.get(name),
                "Recorded evidence changed after collection: " + entry["name"] + "/" + name,
            )
        expected_files.update("cases/" + entry["name"] + "/" + name for name in listed)
        if entry["procedure"] == "case":
            require(
                json.loads((directory / "config.json").read_text()) == entry["config"],
                "Executed definition differs from the planned fixture: " + entry["name"],
            )
            check_case_record(directory, listed, entry["name"])
        files[entry["name"]] = listed
    extra = set(inventory(evidence)) - expected_files
    require(not extra, f"Recorded evidence holds files no entry recorded: {sorted(extra)}")
    return files


def check_runtime_sources(runtime_src: Path, manifest_path: Path) -> None:
    """Detects edits to the packaged runtime a candidate could make in its container; not a sandbox."""
    expected = json.loads(manifest_path.read_text())
    suffixes = set(expected["suffixes"])
    present = {
        str(path.relative_to(runtime_src)): sha256_bytes(path.read_bytes())
        for path in sorted(runtime_src.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in suffixes
    }
    require(present == expected["files"], "Runtime sources differ from the packaged runtime")


def judge_entries(
    scenario: str,
    entries: list[dict],
    recorded: Recorded,
    cases: dict,
    mode: str,
    report: dict,
    rubric_runs: list[dict],
    fixture: FixtureEvaluator | None = None,
) -> None:
    """Judge each recorded entry; rows are appended first, so a rejection still shows how far the engine got."""
    fixture = evaluator_for(scenario) if fixture is None else fixture
    fixtures = {case["name"]: case for kind in ("positive", "negative") for case in cases[kind]}
    for entry in entries:
        artifact_dir = recorded.case(entry["name"])
        run = json.loads((artifact_dir / "case.json").read_text())
        candidate = entry["config"]
        row: dict[str, Any] = {
            "name": entry["name"],
            "kind": entry["kind"],
            "passed": False,
            "execution_id": run.get("execution_id"),
            "workflow_id": run.get("workflow_id"),
            "n8n_version": run.get("n8n_version"),
            "artifacts": str(artifact_dir),
        }
        if entry["kind"] in ("positive", "negative"):
            row["deadline_seconds"] = candidate["execution"]["deadline_seconds"]
            row["config_sha256"] = sha256_bytes((artifact_dir / "config.json").read_bytes())
        row.update(execution_summary(run))
        report["cases"].append(row)
        try:
            if entry["kind"] == "positive":
                case = fixtures[entry["name"]]
                row.update(
                    check_execution(
                        scenario,
                        case["inputs"],
                        run,
                        mode,
                        config=candidate,
                        case=case,
                        rubric_runs=rubric_runs,
                        fixture=fixture,
                    )
                )
                row["acceptance"] = recorded.accept(
                    entry["name"],
                    not row.get("exhausted", False),
                    "Expected refinement exhaustion; no accepted reply" if row.get("exhausted") else None,
                )
                row["verifier_corruptions_rejected"] = (
                    refinement_corruptions(candidate, run, mode=mode)
                    if fixture.procedure == "refinement"
                    else corruption_checks(
                        scenario, case["inputs"], run, mode, config=candidate, case=case, fixture=fixture
                    )
                )
                row["output"] = run["output"]
            elif entry["kind"] == "negative":
                case = fixtures[entry["name"]]
                check_rejection(run, case, candidate, fixture.contract)
                row["acceptance"] = recorded.accept(entry["name"], False, "Expected invalid input rejection")
                row["expected_error"] = case["error"]
            elif entry["kind"] == "mutated-generated-workflow":
                row["name"] = "wrong-result-in-generated-json"
                check_provenance(run)
                require(run.get("status") == "success", "Broken-output probe did not complete n8n execution")
                try:
                    check_execution(
                        scenario,
                        candidate["workflow"]["inputs"],
                        run,
                        mode,
                        config=candidate,
                        case=cases["positive"][0],
                        fixture=fixture,
                    )
                except Rejected as error:
                    row["rejection"] = str(error)
                    row["acceptance"] = recorded.accept(entry["name"], False, str(error))
                else:
                    raise Rejected("Verifier accepted deliberately wrong generated workflow output")
            else:
                row["name"] = entry["name"].removeprefix("invalid-yaml-")
                row["acceptance"] = recorded.accept(entry["name"], False, "Definition rejected before execution")
                require(
                    run.get("status") == "compile_error",
                    "Compiler accepted invalid/unsupported definition: " + row["name"],
                )
                require(
                    not run.get("workflow_id") and not run.get("execution_id"),
                    "Rejected definition was imported or executed",
                )
        except Rejected as error:
            if entry["kind"] in ("positive", "negative"):
                row["acceptance"] = recorded.accept(entry["name"], False, str(error), error.code)
            raise
        row["passed"] = True


def evaluate(
    scenario: str,
    config_path: Path,
    evidence: Path,
    cases: dict | None,
    mode: str = "stub",
    selected_case: str | None = None,
    *,
    runtime_src: Path | None = None,
    runtime_manifest: Path | None = None,
    judge: Judge | None = None,
    evaluation: Path | None = None,
    deadline_budget: int | None = None,
    fixture: FixtureEvaluator | None = None,
) -> dict:
    """Judge the recorded observation of one submission; decisions go beside it unless `evaluation` is given."""
    evaluation = evaluation or evidence.parent / "evaluation"
    evaluation.mkdir(parents=True, exist_ok=True)
    fixture = evaluator_for(scenario) if fixture is None else fixture
    identity = fixture.identity or evaluator_identity(scenario)
    rubric_runs: list[dict] = []
    procedure = "case"
    report: dict[str, Any] = {
        "scenario": scenario,
        "mode": mode,
        "passed": False,
        "report_schema": "sapi-lab-verification/v1",
        "passed_means": "All expected positive acceptances and deliberate rejection probes passed",
        "evaluator": identity,
        "cases": [],
        "limits": [
            "Checks the supported static sapi-lab/v0 profile only.",
            "Live report checks source evidence and dataflow; natural-language semantic completeness is not proven.",
        ],
    }
    try:
        if runtime_src is not None:
            require(runtime_manifest is not None and runtime_manifest.is_file(), "Missing packaged runtime manifest")
            assert runtime_manifest is not None
            check_runtime_sources(runtime_src, runtime_manifest)
        expected = plan(scenario, config_path, cases, mode, selected_case, deadline_budget, fixture)
        assert cases is not None
        report["submission_sha256"] = expected["submission_sha256"]
        report["fixture_sha256"] = expected["fixture_sha256"]
        report["config_transformations"] = ["workflow.inputs replaced by case fixture"] + (
            ["execution.deadline_seconds set to 600"] if mode == "live" else []
        )
        recorded = Recorded(evidence, evaluation, read_evidence(evidence, expected), identity)
        procedure = fixture.procedure
        report["observation"] = {"manifest": "evidence/observation.json", "plan_sha256": digest(expected)}
        if fixture.procedure == "lifecycle":
            if TYPE_CHECKING or __package__:
                from .lifecycle_submission import evaluate_lifecycle
            else:
                from lifecycle_submission import evaluate_lifecycle
            evaluate_lifecycle(expected["entries"], recorded, report["cases"])
        else:
            judge_entries(scenario, expected["entries"], recorded, cases, mode, report, rubric_runs, fixture)
        report["passed"] = all(row["passed"] for row in report["cases"])
    except Exception as error:  # Candidate evidence can fail any way; each way is a rejection
        report["error"] = str(error)
        report["error_type"] = type(error).__name__
        if isinstance(error, Rejected) and error.code:
            report["error_code"] = error.code
        report["passed"] = False
    if procedure != "lifecycle":
        # Outside the acceptance try: the rubric reads the verdict and never sets it.
        executions = sum(row["kind"] == "positive" for row in report["cases"])
        executed = bool(rubric_runs) and len(rubric_runs) == executions
        try:
            scored = score_rubric(
                scenario,
                rubric_runs,
                accepted=report["passed"],
                execution_pass=executed,
                judge=judge,
                card=fixture.rubric,
            )
        except Exception as error:  # A rubric must never cost a correct submission its report
            scored = {
                "schema": SCHEMA,
                "status": NOT_EVALUATED,
                "score_0_10": None,
                "normalized_reward": None,
                "reason": "Not scored: " + type(error).__name__ + ": " + str(error),
            }
        if scored is not None:
            (evaluation / "evaluation.json").write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n")
    (evaluation / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def load_cases(path: Path, scenario: str) -> dict | None:
    return json.loads(path.read_text()).get(scenario) if path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["plan", "evaluate"])
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--output", type=Path, help="plan: where to write the plan")
    parser.add_argument("--evidence", type=Path, help="evaluate: the recorded observation directory")
    parser.add_argument("--runtime-src", type=Path, help="evaluate: the runtime that executed the plan")
    parser.add_argument("--mode", choices=["stub", "live"], default=os.environ.get("SAPI_LLM_MODE", "stub"))
    parser.add_argument("--case", default=os.environ.get("SAPI_CASE_NAME"))
    args = parser.parse_args()
    cases = load_cases(args.cases, args.scenario)
    budget = args.cases.with_name("budget.json")
    deadline_budget = json.loads(budget.read_text())["deadline_seconds"] if budget.is_file() else None
    if args.action == "plan":
        if args.output is None:
            parser.error("plan requires --output")
        try:
            issued = plan(args.scenario, args.config, cases, args.mode, args.case, deadline_budget)
        except Exception as error:  # An unplannable submission runs nothing and fails
            print(json.dumps({"planned": False, "error": f"{type(error).__name__}: {error}"}))
            return 1
        args.output.write_text(json.dumps(issued, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"planned": True, "entries": len(issued["entries"])}))
        return 0
    if args.evidence is None:
        parser.error("evaluate requires --evidence")
    report = evaluate(
        args.scenario,
        args.config,
        args.evidence,
        cases,
        args.mode,
        args.case,
        runtime_src=args.runtime_src,
        runtime_manifest=args.cases.with_name("runtime-sources.json") if args.runtime_src else None,
        deadline_budget=deadline_budget,
    )
    print(
        json.dumps(
            {
                "scenario": report["scenario"],
                "passed": report["passed"],
                "case_count": len(report["cases"]),
                "error": report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
