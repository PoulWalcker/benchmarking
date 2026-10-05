"""Frozen fixture overlays and durable admission for the four-scenario pilot.

No provider transport lives here. Runners reserve one bounded attempt before
launching it and close that reservation from observed acceptance. An unfinished
reservation is an unknown outcome and blocks further work in the same series.
"""

from __future__ import annotations

import copy
import fcntl
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import secrets

from sapi_config_lab.core.provenance import source_manifest
from sapi_config_lab.experiments.replay import read_json, require
from sapi_config_lab.paths import workspace_root
from sapi_config_lab.core.scenarios import EXPANSION_SCENARIOS

RUNTIME_CAPS = {
    "dual-ledger-closeout": {"base-ledgers": 0, "alternate-ledgers": 0},
    "support-review-packet": {"high-packet": 2, "normal-packet": 2},
    "bulletin-market-brief": {"base-bulletins": 4, "alternate-bulletins": 4},
    "priority-support-brief": {"high-brief": 4, "normal-empty": 1},
}


def select_series_scenarios(scenarios: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """A subset retains the registry's order; omission selects the complete series."""
    selected = tuple(EXPANSION_SCENARIOS) if scenarios is None else scenarios
    require(
        isinstance(selected, tuple) and selected and all(isinstance(name, str) for name in selected),
        "Expansion series selection must be a nonempty ordered scenario tuple",
    )
    require(len(set(selected)) == len(selected), "Duplicate expansion series scenario")
    require(all(name in EXPANSION_SCENARIOS for name in selected), "Unknown expansion series scenario")
    require(
        selected == tuple(name for name in EXPANSION_SCENARIOS if name in selected),
        "Expansion series scenarios must follow canonical order",
    )
    return selected


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with (self.directory / ".series.lock").open("a") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Another expansion process owns the series ledger") from error
            try:
                return method(self, *args, **kwargs)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    return locked


def fresh_case_overlay(scenario: str) -> dict:
    """Fresh public-contract values after prompt freeze; no new acceptance rules."""
    require(scenario in EXPANSION_SCENARIOS, "Unknown expansion fixture scenario")
    canonical = read_json(workspace_root() / "verification/cases.json")[scenario]
    cases = copy.deepcopy(canonical)
    token = secrets.token_hex(6).upper()
    increment = secrets.randbelow(400) + 31

    # Stable replacements preserve duplicate-ID controls and sibling metamorphisms.
    def transform(value, *, field="", positive=False):
        if isinstance(value, list):
            return [transform(item, field=field, positive=positive) for item in value]
        if isinstance(value, dict):
            return {key: transform(item, field=key, positive=positive) for key, item in value.items()}
        if isinstance(value, str):
            if field in {"id", "required_order_id"}:
                return token + "-" + value
            if field in {"text", "product_material", "marketing_material"} and value:
                return "Fixture " + token + ": " + value
        if field == "amount_minor" and positive and isinstance(value, int) and 0 < value < 1_000_000:
            return value + increment
        return value

    for kind in ("positive", "negative"):
        for case in cases[kind]:
            original = case["inputs"]
            case["inputs"] = transform(original, positive=kind == "positive")
            if "required_order_id" in original:
                # Keep the order named in the ticket aligned with the projected request.
                case["inputs"]["ticket"]["text"] = case["inputs"]["ticket"]["text"].replace(
                    original["required_order_id"], case["inputs"]["required_order_id"]
                )
    return {scenario: cases}


class ExpansionSeries:
    """One durable ordered selection, with strictly derived authoring/runtime caps."""

    schema = "sapi-lab-expansion-series/v2"
    authoring_attempts = 2
    runtime_caps = RUNTIME_CAPS
    select = staticmethod(select_series_scenarios)

    def __init__(self, directory: Path, *, scenarios: tuple[str, ...] | None = None):
        self.scenarios = self.select(scenarios)
        self.ceilings = {
            "authoring": self.authoring_attempts * len(self.scenarios),
            "runtime": sum(sum(self.runtime_caps[name].values()) for name in self.scenarios),
        }
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "series.json"
        frozen = source_manifest()
        if self.path.exists():
            self.data = read_json(self.path)
            self._validate()
            require(self.data.get("source_manifest") == frozen, "Expansion series sources changed")
        else:
            self.data = {
                "schema": self.schema,
                "source_manifest": frozen,
                "scenarios": list(self.scenarios),
                "ceilings": dict(self.ceilings),
                "events": [],
                "failed": False,
            }
            self._save(exclusive=True)

    def _validate(self):
        require(self.data.get("schema") == self.schema, "Unknown series policy; start a fresh ledger")
        require(self.data.get("scenarios") == list(self.scenarios), "Frozen expansion scenario selection changed")
        require(
            self.data.get("ceilings") == self.ceilings
            and all(type(value) is int for value in self.data["ceilings"].values()),
            "Frozen expansion ceilings changed",
        )
        events = self.data.get("events")
        require(isinstance(events, list), "Invalid expansion event ledger")
        planned: list[tuple[str, str, str, int]] = []
        for scenario in self.scenarios:
            planned.extend((scenario, "authoring", str(number), 1) for number in range(1, self.authoring_attempts + 1))
            planned.extend((scenario, "runtime", name, count) for name, count in self.runtime_caps[scenario].items())
        require(len(events) <= len(planned), "Too many expansion events")
        for index, event in enumerate(events):
            require(
                isinstance(event, dict) and set(event) == {"scenario", "phase", "name", "count", "status", "report"},
                "Invalid expansion event fields",
            )
            require(
                tuple(event[key] for key in ("scenario", "phase", "name", "count")) == planned[index]
                and type(event["count"]) is int,
                "Expansion event differs from frozen order or count",
            )
            require(
                event["status"] in {"passed", "failed", "unknown"} and isinstance(event["report"], str),
                "Invalid expansion event outcome",
            )
            require(
                index == len(events) - 1 or event["status"] == "passed", "Work continued after a nonpassing attempt"
            )
        require(
            type(self.data.get("failed")) is bool
            and self.data["failed"] == any(event["status"] == "failed" for event in events),
            "Expansion failure latch differs from its events",
        )

    def _save(self, *, exclusive=False):
        payload = json.dumps(self.data, indent=2) + "\n"
        if exclusive:
            with self.path.open("x") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            temporary = self.path.with_suffix(".tmp")
            with temporary.open("w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)

    @_serialized
    def reserve(self, scenario: str, phase: str, name: str, report: Path) -> int:
        # Re-read before every reservation: a previous unknown outcome fails closed.
        self.data = read_json(self.path)
        self._validate()
        require(self.data["source_manifest"] == source_manifest(), "Expansion series sources changed")
        require(not self.data["failed"], "Expansion series failure latch is set")
        events = self.data["events"]
        require(all(event["status"] == "passed" for event in events), "An expansion attempt has an unknown outcome")
        ordered = self.scenarios
        require(scenario in ordered, "Scenario is not selected for this expansion series")
        require(phase in {"authoring", "runtime"}, "Unknown expansion admission")
        earlier = ordered[: ordered.index(scenario)]
        for previous in earlier:
            completed = {
                event["name"] for event in events if event["scenario"] == previous and event["phase"] == "runtime"
            }
            require(completed == set(self.runtime_caps[previous]), "Previous scenario has not passed its live gate")
            reports = {
                event["report"] for event in events if event["scenario"] == previous and event["phase"] == "runtime"
            }
            for path in reports:
                require(Path(path).is_file(), "Previous scenario has no final live report")
                final = read_json(Path(path))
                require(
                    final.get("status") == "passed"
                    and final.get("scenario") == previous
                    and final.get("source_unchanged") is True,
                    "Previous scenario final live gate failed",
                )
        own = [event for event in events if event["scenario"] == scenario and event["phase"] == phase]
        require(name not in {event["name"] for event in own}, "An expansion attempt cannot be repeated")
        if phase == "authoring":
            require(
                name == str(len(own) + 1) and len(own) < self.authoring_attempts,
                "Authoring exceeds frozen ordered attempts",
            )
            count = 1
        else:
            authored = [event for event in events if event["scenario"] == scenario and event["phase"] == "authoring"]
            require(len(authored) == self.authoring_attempts, "Runtime requires all accepted authoring attempts")
            names = list(self.runtime_caps[scenario])
            require(len(own) < len(names) and name == names[len(own)], "Runtime cases must follow the frozen order")
            count = self.runtime_caps[scenario][name]
        spent = sum(event["count"] for event in events if event["phase"] == phase)
        require(spent + count <= self.data["ceilings"][phase], "Expansion series attempt ceiling exhausted")
        events.append(
            {
                "scenario": scenario,
                "phase": phase,
                "name": name,
                "count": count,
                "status": "unknown",
                "report": str(report.resolve()),
            }
        )
        self._save()
        return len(events) - 1

    @_serialized
    def finish(self, reservation: int, passed: bool) -> None:
        self.data = read_json(self.path)
        self._validate()
        event = self.data["events"][reservation]
        require(event["status"] == "unknown", "Expansion reservation already closed")
        event["status"] = "passed" if passed else "failed"
        self.data["failed"] = not passed
        self._save()


def overlay_sha256(cases: dict) -> str:
    return hashlib.sha256((json.dumps(cases, indent=2) + "\n").encode()).hexdigest()


class RefinementSeries(ExpansionSeries):
    """Separate reply-refinement policy; never reinterprets an expansion ledger.

    Runtime reservations are upper bounds. Native acceptance can stop before the
    third attempt, while actual dispatch counts remain in the correlated report.
    """

    schema = "sapi-lab-refinement-series/v1"
    authoring_attempts = 3
    runtime_caps = {"revise-answer": {"valid-reply": 3, "impossible-limit": 3}}

    @staticmethod
    def select(scenarios: tuple[str, ...] | None = None) -> tuple[str, ...]:
        require(scenarios is None or scenarios == ("revise-answer",), "Refinement series admits revise-answer only")
        return ("revise-answer",)
