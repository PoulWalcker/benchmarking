"""Independent acceptance primitives and engine-neutral workflow observations."""

from dataclasses import dataclass
from typing import Any


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
