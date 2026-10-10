"""Independently verify ticket routing, both branches and recorded n8n evidence."""

import copy
import json
from pathlib import Path

from verification import verify
from verification.contracts import WorkflowObservation, equal, require
from verification.fixture import FixtureEvaluator
from verification.n8n_provenance import rows
from verification.rubric import Criterion, RubricCard

ROOT = Path(__file__).resolve().parent.parent


def route_ticket(
    inputs: dict, observation: WorkflowObservation, mode: str = "stub", *, case: dict | None = None
) -> None:
    """Compare semantic expectations to native observed events, not to the task's JavaScript."""
    ticket = inputs["ticket"]
    priority = "high" if ticket["days_overdue"] > 2 else "normal"
    branch = "escalate" if priority == "high" else "normal"
    other = "normal" if priority == "high" else "escalate"
    expected = {
        "ticket_id": ticket["id"],
        "action": "escalate" if priority == "high" else "normal_reply",
        "mode": "draft",
    }
    ids = observation.roles
    events = observation.events
    states = observation.states
    require(set(ids) == {"classify", "escalate", "normal", "select"}, "Unbound routing roles")
    require(events[ids["classify"]]["status"] == "completed", "Classification did not complete")
    require(events[ids[branch]]["status"] == "completed", "Chosen branch did not execute")
    require(events[ids[other]]["status"] == "skipped", "Unselected branch was not skipped")
    require(events[ids["select"]]["status"] == "completed", "Final selection did not execute")
    equal(
        states[ids["classify"]]["steps"][ids["classify"]],
        {"category": "delivery", "priority": priority},
        "Wrong ticket classification",
    )
    equal(states[ids[branch]]["steps"][ids[branch]], expected, "Wrong branch draft")
    require(ids[other] not in states[ids[other]].get("steps", {}), "Skipped branch returned output")
    equal(states[ids["select"]]["steps"][ids["select"]], expected, "Wrong selected draft")
    equal(observation.final["output"], expected, "Wrong final ticket action")


def corrupt_action(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"]["action"] = "normal_reply" if final["output"]["action"] == "escalate" else "escalate"
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
        business=route_ticket,
        corrupt_output=corrupt_action,
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
        "ticket-routing",
        submission,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        options.get("deadline_seconds", 120),
        fixture=_fixture(options),
    )


def evaluate(evidence: Path, options: dict) -> dict:
    report = verify.evaluate(
        "ticket-routing",
        Path(options["submission"]),
        evidence,
        _cases(options),
        options.get("mode", "stub"),
        options.get("selected_case"),
        deadline_budget=options.get("deadline_seconds", 120),
        fixture=_fixture(options),
        evaluation=Path(options["evaluation"]),
    )
    executions = [row["execution"]["succeeded"] for row in report["cases"] if row["kind"] == "positive"]
    quality = json.loads((Path(options["evaluation"]) / "evaluation.json").read_text())
    return {"execution": all(executions) if executions else None, "acceptance": report["passed"], "quality": quality}
