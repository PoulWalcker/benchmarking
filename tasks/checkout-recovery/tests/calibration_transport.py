"""Adapt immutable simulated calibration answers to a new recorded attempt, without judging it."""

import hashlib
import json
from pathlib import Path

from sapi_config_lab.evidence import digest

TEMPLATE_SHA256 = "0af5a1b5ffd8cc704cb67a0c4a15afbbebf95b4a3389216f735699c15cbc178b"
ADAPTER = "sapi-native-saved-calibration/v1"


def adapt(template: Path, contract, run_log: dict) -> tuple[dict | None, dict]:
    raw = template.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Saved calibration template identity mismatch")
    saved = json.loads(raw)
    card = contract.package["scorecard"]
    if (
        contract.judge_mode != "demo"
        or saved["provenance"]["mode"] != "demo"
        or saved["provenance"]["model"] != "SIMULATED"
        or saved["provenance"]["prompt_version"] != contract.prompt_version
        or saved["judgement"]["scorecard_digest"] != digest(card)
        or run_log["challenge"]["hashes"]["scorecard"] != digest(card)
    ):
        raise ValueError("Saved calibration differs from the frozen scorecard/judge contract")
    answers = {row["criterion_id"]: row["answer"] for row in saved["judgement"]["criteria"]}
    criteria = [row for row in card["criteria"] if row["evaluator"] == "llm"]
    if set(answers) != {row["id"] for row in criteria}:
        raise ValueError("Saved calibration criterion identity mismatch")
    request = {
        "adapter": ADAPTER,
        "template_sha256": TEMPLATE_SHA256,
        "contract_digest": contract.as_dict()["contract_digest"],
        "run_log_digest": digest(run_log),
    }
    audit = {
        **request,
        "request_digest": digest(request),
        "original_provenance": saved["provenance"],
        "original_run_id": saved["judgement"]["run_id"],
        "paid_dispatch": False,
        "judge_dispatches": 0,
        "meaning": "New simulated reply using saved answer values; not historical replay or measured model quality",
    }
    evidence = {event["source"]: event["id"] for event in reversed(run_log["events"])}
    if any(not set(row["required_evidence"]) <= evidence.keys() for row in criteria):
        return None, {**audit, "status": "missing_required_evidence"}
    judgement = {
        "run_id": run_log["run_id"],
        "scorecard_digest": digest(card),
        "status": "complete",
        "criteria": [
            {
                "criterion_id": row["id"],
                "answer": answers[row["id"]],
                "reason": "SAVED SIMULATED CALIBRATION: fixed template answer, not a judgement of this attempt's prose.",
                "evidence_refs": [evidence[source] for source in row["required_evidence"]],
            }
            for row in criteria
        ],
        "issues": ["Derived from immutable saved simulation; no model evaluated this attempt."],
    }
    reply = {
        "judgement": judgement,
        "provenance": {
            "mode": "demo",
            "model": "SIMULATED",
            "prompt_version": contract.prompt_version,
            "prompt_digest": digest(request),
            "run_log_digest": digest(run_log),
            "response_digest": digest(judgement),
            "judged_at": run_log["finished_at"],
            "duration_seconds": 0,
            "artifact_id": "saved-calibration-" + digest(request)[:16],
        },
    }
    return reply, {**audit, "status": "adapted", "reply_digest": digest(reply)}
