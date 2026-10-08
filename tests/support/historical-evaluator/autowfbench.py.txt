"""Frozen upstream task contracts and an adapter to the unchanged upstream scorer and judge.

Host-only: candidates never see the package, its checks or the judge. The pinned
source is verified before and after every upstream invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from sapi_config_lab.contracts import OutputArtifact
from sapi_config_lab.pinned_source import PinnedSource

Document = dict[str, Any]
SCHEMA = "sapi-lab-task-evaluation/v1"
PROMPT_VERSION = "1.0.1"
WORKER_TIMEOUT_SECONDS = 30
JUDGE_TIMEOUT_SECONDS = 180
# Run validation, judge validation, result validation, scoring and one rejected-judgement fallback.
EVALUATION_TIMEOUT_SECONDS = 5 * WORKER_TIMEOUT_SECONDS + JUDGE_TIMEOUT_SECONDS + 15 + 30


def digest(value: Any) -> str:
    """Use the upstream canonical JSON digest, including ASCII escaping."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def normalized_reward(score_0_10: float) -> float:
    """Divide the upstream score by ten in Decimal, as upstream scoring does.

    Binary float division disagrees for 289 of the 1001 reachable two-decimal
    scores (0.07 / 10 is 0.007000000000000001).
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


def _environment(source: PinnedSource, request: Document) -> dict[str, str]:
    root = str(source.root.resolve())
    pinned = {"PYTHONPATH": root, "AUTOWFBENCH_ROOT": root, "PYTHONDONTWRITEBYTECODE": "1"}
    if request["operation"] == "judge":
        # The judge CLI needs the user's own configuration and credentials; simulator tokens never pass.
        return {**{k: v for k, v in os.environ.items() if not k.startswith("AWB_")}, **pinned}
    return {"PATH": os.defpath, **pinned}


def _upstream(source: PinnedSource, request: Document, timeout: float = WORKER_TIMEOUT_SECONDS) -> Document:
    before = source.verify()
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER],
        input=json.dumps(request, allow_nan=False),
        text=True,
        capture_output=True,
        cwd=source.root,
        env=_environment(source, request),
        timeout=timeout,
        check=False,
    )
    if source.verify() != before:
        raise ValueError("Pinned evaluator source changed during evaluation")
    if completed.returncode:
        # stderr may hold candidate text or credentials; keep only its digest.
        raise ValueError(
            "Upstream evaluator failed; stderr sha256=" + hashlib.sha256(completed.stderr.encode()).hexdigest()
        )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise ValueError("Upstream evaluator returned a non-object")
    return result


@dataclass(frozen=True)
class FrozenTaskContract:
    """Rubric, data and judge identity frozen before solving.

    `artifact` is the project's own output requirement; it is outside the digest
    because it never affects the upstream score.
    """

    source: PinnedSource
    package_json: str
    source_json: str
    judge_model: str | None
    judge_mode: str
    prompt_version: str = PROMPT_VERSION
    artifact: OutputArtifact | None = None

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
        if self.source.verify() != json.loads(self.source_json):
            raise ValueError("Frozen evaluator identity mismatch")
        if self.prompt_version != PROMPT_VERSION:
            raise ValueError("Unsupported judge prompt version")
        package = self.package
        if package["hashes"] != {key: digest(package[key]) for key in ("definition", "environment", "scorecard")}:
            raise ValueError("Frozen package digest mismatch")
        validate_task_package(package)


def validate_task_package(package: Document) -> None:
    """Reject evaluator and metric types this adapter does not support, rather than guess how to grade them."""
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


def freeze_contract(
    source: PinnedSource,
    challenge: str,
    *,
    judge_model: str | None = None,
    judge_mode: str = "codex",
    artifact: OutputArtifact | None = None,
) -> FrozenTaskContract:
    if judge_mode not in {"codex", "demo"}:
        raise ValueError("Judge mode must be codex or explicit demo")
    if judge_mode == "codex" and (not isinstance(judge_model, str) or not judge_model.strip()):
        raise ValueError("Pin a semantic judge model before solving")
    identity = source.verify()
    package = _upstream(source, {"operation": "package", "challenge": challenge})
    contract = FrozenTaskContract(
        source,
        json.dumps(package, sort_keys=True),
        json.dumps(identity, sort_keys=True),
        judge_model,
        judge_mode,
        artifact=artifact,
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
    """Join the trusted session's finalize() evidence with explicitly labelled candidate claims."""
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
    _upstream(contract.source, {"operation": "validate", "run_log": run})


def judge(
    contract: FrozenTaskContract, run_log: Document, artifact_dir: Path, *, timeout: int = JUDGE_TIMEOUT_SECONDS
) -> Document:
    """Invoke the upstream judge; a codex judge is a model call the caller has reserved."""
    _validate_run(contract, run_log)
    package = contract.package
    return _upstream(
        contract.source,
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


def judgement_fault(contract: FrozenTaskContract, run_log: Document, reply: Document) -> str | None:
    """Why a judge reply does not belong to this frozen judge and run, or None."""
    try:
        provenance, judgement = reply["provenance"], reply["judgement"]
        expected_model = contract.judge_model if contract.judge_mode == "codex" else "SIMULATED"
        if (
            provenance["mode"] != contract.judge_mode
            or provenance["model"] != expected_model
            or provenance["prompt_version"] != contract.prompt_version
            or provenance["run_log_digest"] != digest(run_log)
            or provenance["response_digest"] != digest(judgement)
        ):
            return "Judge provenance differs from frozen contract/evidence"
    except KeyError, TypeError, ValueError:
        return "Judge provenance validation failed"
    return None


def evaluate(
    contract: FrozenTaskContract,
    run_log: Document,
    judge_reply: Document | None = None,
    *,
    judge_error: str | None = None,
) -> Document:
    """Upstream arithmetic and execution_pass, unchanged; never a manufactured total."""
    _validate_run(contract, run_log)
    judgement, provenance = None, None
    if judge_reply is not None:
        reply = judge_reply.get("judgement") if isinstance(judge_reply, dict) else None
        if judgement_fault(contract, run_log, judge_reply) is None and isinstance(reply, dict) and "status" in reply:
            provenance, judgement = judge_reply["provenance"], reply
            if judgement["status"] != "complete":
                judge_error = "Judge reported incomplete evidence"
        else:
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
        score = _upstream(contract.source, request)
    except ValueError:
        if judgement is None:
            raise
        request.update(judgement=None, provenance=None, error="Judge response validation failed")
        score = _upstream(contract.source, request)
    submission = run_log["submission"]
    project_acceptance = None
    if contract.artifact is not None:
        name = contract.artifact.name
        project_acceptance = {
            "criterion": "named_artifact",
            "artifact": name,
            "passed": bool(submission and any(a["name"] == name for a in submission["artifacts"])),
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


def recorded_run_log(contract: FrozenTaskContract, evidence: Path) -> Document:
    """The upstream run log of one simulator trial, from its recorded evidence only."""
    trial = json.loads((evidence / "trial.json").read_text())
    return build_run_log(
        contract,
        json.loads((evidence / "environment-evidence.json").read_text()),
        trial["submission"],
        run_id=trial["run_id"],
        seed=trial["seed"],
        started_at=trial["started_at"],
        finished_at=trial["finished_at"],
        duration_seconds=trial["duration_seconds"],
        termination_reason=trial["termination_reason"],
        solution=trial["solution"],
    )


def evaluate_once(
    contract: FrozenTaskContract,
    run_log: Document,
    output: Path,
    *,
    judgement: Document | None = None,
    dispatch: bool = False,
) -> Document:
    """Score into a fresh directory; the judge is consulted only when `dispatch` is set."""
    if dispatch and judgement is not None:
        raise ValueError("Either dispatch the judge or supply a saved judgement, not both")
    if judgement is not None and (fault := judgement_fault(contract, run_log, judgement)):
        raise ValueError(fault)
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "task-contract.json", contract.as_dict())
    _write(output / "run-log.json", run_log)
    reply, error = judgement, None
    if dispatch:
        dispatched = {"attempts": 1, "mode": contract.judge_mode, "model": contract.judge_model}
        _write(output / "judge-dispatch.json", {**dispatched, "started_at": datetime.now(UTC).isoformat()})
        try:
            reply = judge(contract, run_log, output / "judge")
            _write(output / "judge-reply.json", reply)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            error = type(exc).__name__
    report = evaluate(contract, run_log, reply, judge_error=error)
    write_evaluation(output, report)
    return report


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_evaluation(report_dir: Path, report: Document) -> None:
    """Persist one trial once. An unscored trial has no reward file: not evaluated is not zero."""
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
