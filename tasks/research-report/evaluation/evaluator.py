"""Independent fact/graph/evidence evaluator for the multi-LLM research scenario."""

import copy
import json
from pathlib import Path

from verification import verify
from verification.contracts import WorkflowObservation, equal, require
from verification.fixture import FixtureEvaluator
from verification.n8n_provenance import rows
from verification.rubric import Criterion, Judge, RubricCard

ROOT = Path(__file__).resolve().parent.parent


def _value(observation: WorkflowObservation, role: str) -> dict:
    sid = observation.roles[role]
    require(observation.events[sid]["status"] == "completed", "Research step did not complete: " + role)
    return observation.states[sid]["steps"][sid]


def _ground(inputs: dict, observation: WorkflowObservation) -> None:
    material = inputs["material"]
    equal(_value(observation, "validate"), {"material": material}, "Validation lost source material")
    product, marketing = (_value(observation, key) for key in ("product", "marketing"))
    for name, analysis in (("product", product), ("marketing", marketing)):
        require(
            isinstance(analysis.get("summary"), str) and bool(analysis["summary"].strip()), name + " summary is empty"
        )
        evidence = analysis.get("evidence")
        require(
            isinstance(evidence, str) and bool(evidence) and evidence in material, name + " evidence not from source"
        )
    equal(_value(observation, "combine"), {"product": product, "marketing": marketing}, "Join changed analyses")
    finished = _value(observation, "write")
    require(isinstance(finished.get("report"), str) and bool(finished["report"].strip()), "Final report is empty")
    equal(finished.get("evidence"), [product["evidence"], marketing["evidence"]], "Final evidence changed")
    equal(observation.final["output"], finished, "Wrong final research report")


def _coverage(observation: WorkflowObservation, case: dict | None) -> None:
    require(case is not None, "Missing independently declared fixture expectations")
    assert case is not None
    report = _value(observation, "write")["report"].casefold()
    for term in case["terms"]:
        require(term.casefold() in report, "Report omitted a source fact: " + term)


def research_report(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    _ground(inputs, observation)
    _coverage(observation, case)


def obligations(inputs: dict, observation: WorkflowObservation, *, case: dict | None = None) -> dict:
    return {
        "grounding": lambda: _ground(inputs, observation),
        "coverage": lambda: _coverage(observation, case),
    }


def prose(inputs: dict, observation: WorkflowObservation, checks: dict) -> dict:
    finished = _value(observation, "write")
    return {
        "environment": "Supplied material:\n" + inputs["material"],
        "candidate": "Final report:\n" + finished["report"],
    }


def corrupt_report(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"] = {"report": "CORRUPTED", "evidence": []}
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


def card() -> RubricCard:
    document = json.loads((ROOT / "evaluation/rubric.json").read_text())
    return RubricCard(
        id=document["id"],
        version=document["version"],
        origin=document["origin"],
        criteria=tuple(Criterion(**criterion) for criterion in document["criteria"]),
    )


def _fixture(options: dict) -> FixtureEvaluator:
    contract = json.loads((ROOT / "evaluation/contract.json").read_text())
    contract["outputs"] = json.loads((ROOT / "evaluation/outputs.json").read_text())
    sources = {
        path.relative_to(ROOT).as_posix(): verify.sha256_bytes(path.read_bytes())
        for path in sorted((ROOT / "evaluation").glob("*"))
        if path.is_file()
    }
    return FixtureEvaluator(
        business=research_report,
        obligations=obligations,
        prose=prose,
        corrupt_output=corrupt_report,
        contract=contract,
        identity={
            "name": "sapi-lab-independent-verifier",
            "sources_sha256": verify.digest(options.get("identity") or sources),
        },
        rubric=card(),
    )


def _cases(options: dict) -> dict:
    return options.get("cases") or json.loads((ROOT / "cases.json").read_text())


def plan(submission: Path, options: dict) -> dict:
    return verify.plan(
        "research-report",
        submission,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        options.get("deadline_seconds", 120),
        fixture=_fixture(options),
    )


def evaluate(evidence: Path, options: dict, *, judge: Judge | None = None) -> dict:
    report = verify.evaluate(
        "research-report",
        Path(options["submission"]),
        evidence,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        deadline_budget=options.get("deadline_seconds", 120),
        judge=judge,
        fixture=_fixture(options),
        evaluation=Path(options["evaluation"]),
    )
    executions = [row["execution"]["succeeded"] for row in report["cases"] if row["kind"] == "positive"]
    quality = json.loads((Path(options["evaluation"]) / "evaluation.json").read_text())
    # A missing live judge is unknown quality, not a failed/zero-quality answer.
    # Preserve detailed not_evaluated diagnostics in evaluation/evaluation.json.
    measured = quality if quality.get("status") == "complete" else None
    return {"execution": all(executions) if executions else None, "acceptance": report["passed"], "quality": measured}
