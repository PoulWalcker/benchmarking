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


class Rejected(AssertionError):
    """A runtime result does not satisfy the independently stated contract."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise Rejected(message)


@dataclass(frozen=True)
class Recorded:
    """One run's checked evidence, and where decisions about it are written."""

    evidence: Path
    evaluation: Path
    files: dict[str, dict[str, str]]
    evaluator: dict[str, str]

    def case(self, name: str) -> Path:
        return self.evidence / "cases" / name

    def accept(self, name: str, passed: bool, reason: str | None) -> dict:
        """One decision about one recorded entry, naming every file it covered."""
        decision: dict[str, Any] = {"status": "accepted" if passed else "rejected", "passed": passed}
        if reason:
            decision["reason"] = reason
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
