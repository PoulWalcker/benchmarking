"""Independent acceptance primitives and engine-neutral workflow observations."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

ACCEPTANCE_SCHEMA = "sapi-lab-acceptance/v1"


@dataclass(frozen=True)
class WorkflowObservation:
    """Logical values; this object alone does not establish execution provenance."""

    final: dict[str, Any]
    events: dict[str, dict[str, Any]]
    states: dict[str, dict[str, Any]]
    roles: dict[str, str]


def scenario_file(scenario: str, name: str) -> Path | None:
    """A scenario-owned evaluation file: packaged beside this verifier, else in its checkout's benchmark directory."""
    here = Path(__file__).resolve().parent
    packaged = here / "evaluation" / scenario / name
    if packaged.is_file():
        return packaged
    found = list((here.parent / "benchmarks").glob(f"[0-9][0-9]-{scenario}/evaluation/{name}"))
    return found[0] if len(found) == 1 else None


class Rejected(AssertionError):
    """A runtime result does not satisfy the independently stated contract; `code` is stable where one is known."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def require(condition: Any, message: str, code: str | None = None) -> None:
    if not condition:
        raise Rejected(message, code)


@dataclass(frozen=True)
class Recorded:
    """One run's checked evidence, and where decisions about it are written."""

    evidence: Path
    evaluation: Path
    files: dict[str, dict[str, str]]
    evaluator: dict[str, str]

    def case(self, name: str) -> Path:
        return self.evidence / "cases" / name

    def accept(self, name: str, passed: bool, reason: str | None, code: str | None = None) -> dict:
        """One decision about one recorded entry, naming every file it covered."""
        decision: dict[str, Any] = {"status": "accepted" if passed else "rejected", "passed": passed}
        if reason:
            decision["reason"] = reason
        if code:
            decision["code"] = code
        directory = self.evaluation / "cases" / name
        directory.mkdir(parents=True, exist_ok=True)
        document = {
            "schema": ACCEPTANCE_SCHEMA,
            **decision,
            "evidence": self.files[name],
            "evaluator": self.evaluator["name"],
            "evaluator_sha256": self.evaluator["sources_sha256"],
        }
        (directory / "acceptance.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return decision


def equal(actual: Any, expected: Any, message: str) -> None:
    # JSON distinguishes booleans, numbers, null, arrays and objects.
    require(type(actual) is type(expected), message + " (type)")
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), message + " (keys)")
        for key in expected:
            equal(actual[key], expected[key], message + "." + key)
    elif isinstance(expected, list):
        require(len(actual) == len(expected), message + " (length)")
        for index, value in enumerate(expected):
            equal(actual[index], value, f"{message}[{index}]")
    else:
        require(actual == expected, message)
