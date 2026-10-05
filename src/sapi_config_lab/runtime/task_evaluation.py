"""Frozen task contracts and an adapter to the unchanged AutoWFBench evaluator.

Only the trusted orchestrator calls this module. Candidate YAML receives public
instructions and tool capabilities, never the package, checks, or judge interface.
The external source checkout stays outside our distribution; its pinned manifest
is verified before and after every upstream invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

Document = dict[str, Any]
SCHEMA = "sapi-lab-task-evaluation/v1"
PROMPT_VERSION = "1.0.1"
CONFIGURED_TASKS = ("production-checkout-recovery", "crm-lead-qualification")


def digest(value: Any) -> str:
    """Use the upstream canonical JSON digest, including ASCII escaping."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def normalized_reward(score_0_10: float) -> float:
    """Divide the upstream score by ten in Decimal, never in binary float.

    Upstream quantizes `score_0_10` to two decimals before returning a float, so
    `Decimal(str(...))` recovers that exact value. Plain `score_0_10 / 10`
    disagrees with the Decimal quotient for 289 of the 1001 reachable scores
    (0.07 becomes 0.007000000000000001), and 127 of them do not round-trip.
    This matches upstream `autowfbench/core/scoring.py`, which keeps the score
    itself in Decimal for the same reason.
    """
    exact = Decimal(str(score_0_10)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float((exact / Decimal(10)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


_WORKER = """
import json, sys
from pathlib import Path
from autowfbench.core.contracts import load_challenge, validate
from autowfbench.core.scoring import calculate
request = json.load(sys.stdin)
operation = request['operation']
if operation == 'package':
    result = load_challenge(request['challenge'])
elif operation == 'validate':
    result = validate('run-log', request['run_log'])
elif operation == 'seed_inputs':
    from autowfbench.core.common import digest
    from autowfbench.runtime.environment import ChallengeEnvironment
    package = load_challenge(request['challenge'])
    rows = []
    for seed in request['seeds']:
        env = ChallengeEnvironment(package, seed)
        probes = ([case['name'] for case in env.test_results(hidden=True)['cases']]
                  if env.kind == 'checkout' else env.fixtures)
        rows.append({'seed': seed, 'fixture_digest': digest(env.fixtures), 'probe_digest': digest(probes)})
    result = {'rows': rows}
elif operation == 'score':
    validate('run-log', request['run_log'])
    result = calculate(request['run_log'], request['scorecard'], request.get('judgement'),
                       request.get('provenance'), request.get('error'))
elif operation == 'judge':
    from autowfbench.runtime.judge import Judge
    result = Judge(mode=request['mode'], model=request['model'],
                   data_dir=Path(request['artifact_dir']), timeout=request['timeout']).evaluate(request['request'])
else:
    raise ValueError('Unknown trusted evaluation operation')
json.dump(result, sys.stdout, allow_nan=False)
"""


def _source_identity(source_root: Path) -> Document:
    from sapi_config_lab.runtime.autowfbench import verify_source

    return verify_source(source_root)


def _upstream(source_root: Path, request: Document, timeout: float = 30) -> Document:
    before = _source_identity(source_root)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWB_")}
    env.update(PYTHONPATH=str(source_root), AUTOWFBENCH_ROOT=str(source_root), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER],
        input=json.dumps(request, allow_nan=False),
        text=True,
        capture_output=True,
        cwd=source_root,
        env=env,
        timeout=timeout,
        check=False,
    )
    if _source_identity(source_root) != before:
        raise ValueError("Pinned evaluator source changed during evaluation")
    if completed.returncode:
        # External output may contain candidate text or credentials. Retain only
        # its digest here; the judge owns its detailed, trusted artifact directory.
        raise ValueError(
            "Upstream evaluator failed; stderr sha256=" + hashlib.sha256(completed.stderr.encode()).hexdigest()
        )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError("Upstream evaluator returned a non-object")
    return result


@dataclass(frozen=True)
class FrozenTaskContract:
    """Immutable serialized rubric/data plus judge identity, created before solving."""

    source_root: Path
    package_json: str
    source_json: str
    judge_model: str | None
    judge_mode: str
    prompt_version: str = PROMPT_VERSION

    @property
    def package(self) -> Document:
        return json.loads(self.package_json)

    def as_dict(self) -> Document:
        value = {
            "schema": "sapi-lab-task-contract/v1",
            "source": json.loads(self.source_json),
            "package": self.package,
            "judge": {"mode": self.judge_mode, "model": self.judge_model, "prompt_version": self.prompt_version},
            "reward": {"formula": "score_0_10 / 10", "missing_judge": "unscored", "execution_pass": "separate"},
        }
        return {**value, "contract_digest": digest(value)}

    def verify(self) -> None:
        if _source_identity(self.source_root) != json.loads(self.source_json):
            raise ValueError("Frozen evaluator identity mismatch")
        if self.prompt_version != PROMPT_VERSION:
            raise ValueError("Unsupported judge prompt version")
        package = self.package
        if package["hashes"] != {key: digest(package[key]) for key in ("definition", "environment", "scorecard")}:
            raise ValueError("Frozen package digest mismatch")
        validate_task_package(package)


def validate_task_package(package: Document) -> None:
    """Validate supported task/metric semantics before a solver sees the task.

    Business criteria still belong to the original scorecard; this rejects
    unsupported evaluator/metric types rather than guessing how to grade them.
    """
    definition, card = package["definition"], package["scorecard"]
    if definition["id"] != card["id"] or not definition.get("task") or not definition.get("completion"):
        raise ValueError("Task identity/instructions are incomplete")
    limits = definition["limits"]
    if set(limits) != {"wall_clock_seconds", "tool_calls"} or any(
        type(v) is not int or v <= 0 for v in limits.values()
    ):
        raise ValueError("Unsupported task limits")
    capabilities = definition["capabilities"]
    if (
        not capabilities
        or len(capabilities) != len(set(capabilities))
        or any(not isinstance(v, str) or not v for v in capabilities)
    ):
        raise ValueError("Invalid environment capabilities")
    if card["answer_values"] != {"yes": 1, "maybe": 0.33, "no": 0}:
        raise ValueError("Unsupported metric answer values")
    criteria = card["criteria"]
    if not criteria or len({c["id"] for c in criteria}) != len(criteria):
        raise ValueError("Missing or duplicate scoring criteria")
    for criterion in criteria:
        if criterion["evaluator"] not in {"deterministic", "llm"}:
            raise ValueError("Unsupported evaluator")
        if type(criterion["weight"]) not in (int, float) or not 0 < criterion["weight"] <= 10:
            raise ValueError("Invalid criterion weight")
        if not criterion["required_evidence"] or not set(criterion["required_evidence"]) <= {
            "environment",
            "candidate",
            "verification",
            "engine",
        }:
            raise ValueError("Unsupported evidence source")
        if criterion["evaluator"] == "deterministic" and not criterion.get("check"):
            raise ValueError("Deterministic criterion requires an independent check")
    if sum(c["weight"] for c in criteria) != 10:
        raise ValueError("Original benchmark requires a ten-point rubric")


def configured_contracts(
    source_root: Path, *, judge_model: str, judge_mode: str = "codex"
) -> dict[str, FrozenTaskContract]:
    """Both source-backed tasks use the same contract, scorer and compiler seam."""
    return {
        name: freeze_contract(source_root, name, judge_model=judge_model, judge_mode=judge_mode)
        for name in CONFIGURED_TASKS
    }


def seed_variation(contract: FrozenTaskContract, seeds: list[int]) -> Document:
    """Measure fixture/probe variation; seed count is never a new-task count."""
    if not seeds or any(type(seed) is not int or seed < 0 for seed in seeds):
        raise ValueError("Supply nonnegative integer seeds")
    contract.verify()
    rows = _upstream(
        contract.source_root,
        {"operation": "seed_inputs", "challenge": contract.package["definition"]["id"], "seeds": seeds},
    )["rows"]
    return {
        "task_count": 1,
        "seed_count": len(seeds),
        "unique_fixture_count": len({r["fixture_digest"] for r in rows}),
        "unique_protected_probe_count": len({r["probe_digest"] for r in rows}),
        "rows": rows,
        "interpretation": "Repeated task with measured input/probe variation; not independent new business tasks",
    }


def freeze_contract(
    source_root: Path, challenge: str, *, judge_model: str | None = None, judge_mode: str = "codex"
) -> FrozenTaskContract:
    """Freeze original task, fixtures, rubric and explicit judge identity once."""
    if judge_mode not in {"codex", "demo"}:
        raise ValueError("Judge mode must be codex or explicit demo")
    if judge_mode == "codex" and (not isinstance(judge_model, str) or not judge_model.strip()):
        raise ValueError("Pin a semantic judge model before solving")
    root = Path(source_root).resolve()
    source = _source_identity(root)
    package = _upstream(root, {"operation": "package", "challenge": challenge})
    contract = FrozenTaskContract(
        root, json.dumps(package, sort_keys=True), json.dumps(source, sort_keys=True), judge_model, judge_mode
    )
    contract.verify()
    return contract


def build_run_log(
    contract: FrozenTaskContract,
    evidence: Document,
    submission: Document | None,
    *,
    run_id: str,
    seed: int,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    termination_reason: str,
    solution: Document,
) -> Document:
    """Join trusted environment receipts with explicitly labelled candidate claims.

    `evidence` must come directly from the trusted session's finalize(), not from
    a workflow result. Tool actions and checks are never inferred from prose.
    """
    contract.verify()
    package = contract.package
    if submission is not None and (
        submission.get("run_id") != run_id
        or submission.get("status") != ("completed" if termination_reason == "completed" else "failed")
    ):
        raise ValueError("Submission run/status mismatch")
    events = [
        {
            "id": "engine-stopped",
            "source": "engine",
            "timestamp": finished_at,
            "kind": "solution_stopped",
            "data": {"termination_reason": termination_reason, "duration_seconds": duration_seconds},
        },
        *evidence["events"],
    ]
    if submission is not None:
        events.append(
            {
                "id": "candidate-final",
                "source": "candidate",
                "timestamp": finished_at,
                "kind": "final_output",
                "data": {"final_answer": submission["final_answer"], "artifacts": submission["artifacts"]},
            }
        )
        events.extend(
            {
                "id": f"candidate-trace-{i:04d}",
                "source": "candidate",
                "timestamp": finished_at,
                "kind": "reported_trace",
                "data": trace,
            }
            for i, trace in enumerate(submission["trace"])
        )
    events.extend(evidence["verification"])
    run = {
        "schema_version": "1.0",
        "run_id": run_id,
        "challenge": {
            "id": package["definition"]["id"],
            "version": package["definition"]["version"],
            "hashes": package["hashes"],
        },
        "solution": {
            **{key: solution[key] for key in ("id", "name", "version", "runtime")},
            "manifest_digest": digest(solution),
        },
        "seed": seed,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "termination_reason": termination_reason,
        "events": events,
        "snapshots": {"initial": evidence["initial"], "final": evidence["final"]},
        "checks": evidence["checks"],
        "submission": submission,
    }
    _validate_run(contract, run)
    return json.loads(json.dumps(run, allow_nan=False))


def _validate_run(contract: FrozenTaskContract, run: Document) -> None:
    contract.verify()
    package = contract.package
    if run["challenge"] != {
        "id": package["definition"]["id"],
        "version": package["definition"]["version"],
        "hashes": package["hashes"],
    }:
        raise ValueError("Run differs from frozen task contract")
    ids = [event["id"] for event in run["events"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Ambiguous duplicate evidence identifiers")
    _upstream(contract.source_root, {"operation": "validate", "run_log": run})


def judge(contract: FrozenTaskContract, run_log: Document, artifact_dir: Path, *, timeout: int = 180) -> Document:
    """Invoke the original separate judge; callers explicitly authorize model calls."""
    _validate_run(contract, run_log)
    package = contract.package
    return _upstream(
        contract.source_root,
        {
            "operation": "judge",
            "mode": contract.judge_mode,
            "model": contract.judge_model,
            "artifact_dir": str(Path(artifact_dir).resolve()),
            "timeout": timeout,
            "request": {"definition": package["definition"], "scorecard": package["scorecard"], "run_log": run_log},
        },
        timeout=timeout + 15,
    )


def evaluate(
    contract: FrozenTaskContract,
    run_log: Document,
    judge_reply: Document | None = None,
    *,
    judge_error: str | None = None,
) -> Document:
    """Preserve upstream arithmetic and execution_pass; never manufacture a total."""
    _validate_run(contract, run_log)
    judgement, provenance = None, None
    if judge_reply is not None:
        try:
            provenance, judgement = judge_reply["provenance"], judge_reply["judgement"]
            expected_model = contract.judge_model if contract.judge_mode == "codex" else "SIMULATED"
            if (
                provenance["mode"] != contract.judge_mode
                or provenance["model"] != expected_model
                or provenance["prompt_version"] != contract.prompt_version
                or provenance["run_log_digest"] != digest(run_log)
                or provenance["response_digest"] != digest(judgement)
            ):
                raise ValueError("Judge provenance differs from frozen contract/evidence")
            if judgement["status"] != "complete":
                judge_error = "Judge reported incomplete evidence"
        except KeyError, TypeError, ValueError:
            judgement, provenance = None, None
            judge_error = "Judge provenance validation failed"
    request = {
        "operation": "score",
        "run_log": run_log,
        "scorecard": contract.package["scorecard"],
        "judgement": judgement,
        "provenance": provenance,
        "error": judge_error,
    }
    try:
        score = _upstream(contract.source_root, request)
    except ValueError:
        if judgement is None:
            raise
        request.update(judgement=None, provenance=None, error="Judge response validation failed")
        score = _upstream(contract.source_root, request)
    submission = run_log["submission"]
    artifact_present = bool(submission and any(a["name"] == "incident-summary.md" for a in submission["artifacts"]))
    project_acceptance = None
    if contract.package["definition"]["id"] == "production-checkout-recovery":
        project_acceptance = {
            "criterion": "named_incident_summary",
            "passed": artifact_present,
            "affects_upstream_score": False,
            "reason": "Separate task-completion diagnostic; upstream has no deterministic filename gate",
        }
    return {
        "schema": SCHEMA,
        "contract_digest": contract.as_dict()["contract_digest"],
        "run_log_digest": digest(run_log),
        "evaluation_mode": contract.judge_mode,
        **score,
        "normalized_reward": normalized_reward(score["score_0_10"]) if score["status"] == "complete" else None,
        "project_acceptance": project_acceptance,
    }


def write_evaluation(report_dir: Path, report: Document) -> None:
    """Persist one trial once. An unscored trial deliberately has no reward file."""
    destination = Path(report_dir)
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "evaluation.json"
    if report_path.exists() or (destination / "reward.txt").exists() or (destination / "reward.json").exists():
        raise ValueError("Choose a fresh evaluation directory; prior scores are immutable")
    reward = report.get("normalized_reward")
    if reward is not None:
        total: Any = report.get("score_0_10")
        if (
            report.get("status") != "complete"
            or type(reward) not in (float, int)
            or not 0 <= reward <= 1
            or type(total) not in (float, int)
            or reward != normalized_reward(total)
        ):
            raise ValueError("Invalid or incomplete normalized reward")
    elif report.get("status") == "complete":
        raise ValueError("Complete evaluation lacks its normalized reward")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    if reward is not None:
        (destination / "reward.txt").write_text(str(reward) + "\n")


def summarize_evaluations(attempts: list[Document | None]) -> Document:
    """Summarize every dispatched attempt; None preserves an absent evaluation.

    A scored-only mean explicitly names its denominator. Missing judgement is
    never a zero business score, even if Harbor's generic job metric substitutes
    zero for absent rewards. Caller records planned-but-undispatched work apart.
    """
    scored = [report for report in attempts if report and report.get("status") == "complete"]
    rewards = [report["normalized_reward"] for report in scored]
    if any(type(value) not in (int, float) or not 0 <= value <= 1 for value in rewards):
        raise ValueError("Complete attempt lacks a valid normalized reward")
    return {
        "attempted": len(attempts),
        "scored": len(scored),
        "unscored": len(attempts) - len(scored),
        "evaluation_missing": sum(report is None for report in attempts),
        "judge_failed": sum(bool(report and report.get("status") == "judge_failed") for report in attempts),
        "execution_passed": sum(bool(report and report.get("execution_pass") is True) for report in attempts),
        "mean_reward_scored": sum(rewards) / len(rewards) if rewards else None,
        "mean_denominator": "scored attempts only; unscored attempts remain reported",
        "evaluation_modes": sorted({report["evaluation_mode"] for report in attempts if report}),
    }


def summarize_stages(
    authoring: list[Document | None],
    runtimes: list[Document | None],
    judges: list[Document | None],
    *,
    calibrations: list[Document | None] | None = None,
) -> Document:
    """Count dispatched attempts by stage without pooling controls/model scores.

    Supply one record per actual dispatch (None for missing outcome). Optional
    numeric fields are duration_seconds, cli_reported_tokens, cost_usd and
    provider_call_count. Judge callers may supply their provenance dictionary.
    Missing usage is unknown, never a zero-cost claim. Candidate evaluation
    scores belong in summarize_evaluations, separately from reference controls.
    """
    stages = {"authoring": authoring, "runtime": runtimes, "judge": judges, "calibration": calibrations or []}
    result: Document = {}
    for name, attempts in stages.items():
        present = [row for row in attempts if row is not None]
        row: Document = {"attempted": len(attempts), "outcome_missing": len(attempts) - len(present)}
        row["eligible"] = sum(item.get("eligible") is True for item in present) if name == "authoring" else None
        row["failed"] = sum(
            item.get("status") in {"failed", "error", "judge_failed"} or item.get("eligible") is False
            for item in present
        )
        for field in ("duration_seconds", "cli_reported_tokens", "cost_usd", "provider_call_count"):
            observed = [item[field] for item in present if item.get(field) is not None]
            if any(type(value) not in (int, float) or not 0 <= value < float("inf") for value in observed):
                raise ValueError("Invalid stage measurement: " + field)
            row[field] = {
                "observed_sum": sum(observed) if observed else None,
                "observed_count": len(observed),
                "total": sum(observed) if len(observed) == len(attempts) and attempts else None,
            }
        result[name] = row
    return {"stages": result, "total_dispatches": sum(len(rows) for rows in stages.values()), "scores_pooled": False}
