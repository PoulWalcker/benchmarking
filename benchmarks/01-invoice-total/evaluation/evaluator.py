"""Invoice plans and independent expectations over frozen native evidence."""

import copy
import json
from pathlib import Path

from verification import verify
from verification.contracts import WorkflowObservation, equal, require
from verification.fixture import FixtureEvaluator
from verification.n8n_provenance import rows
from verification.rubric import Criterion, RubricCard

ROOT = Path(__file__).resolve().parent.parent


def _produced(observation: WorkflowObservation, operation: str) -> dict:
    matches = [sid for sid, event in observation.events.items() if event["operation"] == operation]
    require(len(matches) == 1, "Missing or duplicate operation")
    sid = matches[0]
    return observation.states[sid]["steps"][sid]


def invoice_total(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    output = observation.final["output"]
    require(
        all(event["status"] == "completed" for event in observation.events.values()), "Unexpected skipped operation"
    )
    invoices = inputs["invoices"]
    expected = {
        "total_minor": sum(x["amount_minor"] for x in invoices),
        "currency": invoices[0]["currency"],
        "invoice_count": len(invoices),
    }
    equal(output, expected, "Wrong invoice result")
    equal(
        _produced(observation, "invoices.validate"), {"invoices": invoices}, "Invoice validation changed/lost invoices"
    )
    equal(
        _produced(observation, "invoices.sum"),
        {
            "amount_minor": expected["total_minor"],
            "currency": expected["currency"],
            "count": expected["invoice_count"],
        },
        "Wrong intermediate sum",
    )
    equal(_produced(observation, "invoices.report"), expected, "Wrong report operation output")


def corrupt_total(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["total_minor"] += 1
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


def _fixture(options: dict) -> FixtureEvaluator:
    contract = json.loads((ROOT / "evaluation/contract.json").read_text())
    contract["outputs"] = json.loads((ROOT / "evaluation/outputs.json").read_text())
    card = json.loads((ROOT / "evaluation/rubric.json").read_text())
    identity = options.get("identity")
    sources = {
        path.relative_to(ROOT).as_posix(): verify.sha256_bytes(path.read_bytes())
        for path in sorted((ROOT / "evaluation").glob("*"))
        if path.is_file()
    }
    return FixtureEvaluator(
        business=invoice_total,
        corrupt_output=corrupt_total,
        contract=contract,
        identity={"name": "sapi-lab-independent-verifier", "sources_sha256": verify.digest(identity or sources)},
        rubric=RubricCard(
            id=card["id"],
            version=card["version"],
            origin=card["origin"],
            criteria=tuple(Criterion(**criterion) for criterion in card["criteria"]),
        ),
    )


def _cases(options: dict) -> dict:
    return options.get("cases") or json.loads((ROOT / "cases.json").read_text())


def plan(submission: Path, options: dict) -> dict:
    return verify.plan(
        "invoice-total",
        submission,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        options.get("deadline_seconds", 30),
        fixture=_fixture(options),
    )


def evaluate(evidence: Path, options: dict) -> dict:
    report = verify.evaluate(
        "invoice-total",
        Path(options["submission"]),
        evidence,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        deadline_budget=options.get("deadline_seconds", 30),
        fixture=_fixture(options),
        evaluation=Path(options["evaluation"]),
    )
    executions = [row["execution"]["succeeded"] for row in report["cases"] if row["kind"] == "positive"]
    quality = json.loads((Path(options["evaluation"]) / "evaluation.json").read_text())
    return {"execution": all(executions) if executions else None, "acceptance": report["passed"], "quality": quality}
