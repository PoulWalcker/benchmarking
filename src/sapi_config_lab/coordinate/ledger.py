"""Durable reservations for paid work: every attempt is reserved before it starts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import subprocess

from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.evidence import canonical, durable_json

SCHEMA = "sapi-lab-ledger/v1"
EVENT_FIELDS = {"phase", "name", "count", "status", "report"}


@dataclass
class Outcome:
    """Set `passed` once the reserved work's own gate has been checked."""

    passed: bool = False


class Ledger:
    def __init__(self, path: Path, data: dict):
        self.path = path
        self.data = data

    @classmethod
    def open(cls, path: Path, *, ceilings: dict[str, int] | None, stop_after_failure: bool | None) -> Ledger:
        """Create a ledger with these ceilings, or reopen one and require the same policy."""
        path = path.resolve()
        if not path.exists():
            if ceilings is None or stop_after_failure is None:
                raise ValueError("A new ledger needs explicit ceilings and a failure policy")
            if not ceilings or any(type(value) is not int or value < 0 for value in ceilings.values()):
                raise ValueError("Ledger ceilings must be non-negative integers")
            data = {
                "schema": SCHEMA,
                "source_manifest": source_manifest(),
                "ceilings": dict(ceilings),
                "stop_after_failure": stop_after_failure,
                "events": [],
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x") as stream:
                stream.write(canonical(data) + "\n")
        ledger = cls(path, json.loads(path.read_text()))
        ledger._validate()
        if ceilings is not None and ledger.data["ceilings"] != ceilings:
            raise ValueError("Ledger ceilings differ from the ones it was created with")
        if stop_after_failure is not None and ledger.data["stop_after_failure"] != stop_after_failure:
            raise ValueError("Ledger failure policy differs from the one it was created with")
        return ledger

    def _validate(self) -> None:
        data = self.data
        if data.get("schema") != SCHEMA:
            raise ValueError("Unknown ledger schema; start a new ledger")
        if data.get("source_manifest") != source_manifest():
            raise ValueError("Sources changed since this ledger was created")
        events = data.get("events")
        if not isinstance(events, list) or any(
            not isinstance(event, dict)
            or set(event) != EVENT_FIELDS
            or event["status"] not in ("passed", "failed", "unknown")
            or type(event["count"]) is not int
            for event in events
        ):
            raise ValueError("Invalid ledger events")
        for phase, ceiling in data["ceilings"].items():
            if self.spent(phase) > ceiling:
                raise ValueError("Ledger events exceed their ceiling")

    def spent(self, phase: str, report: Path | None = None) -> int:
        return sum(
            event["count"]
            for event in self.data["events"]
            if event["phase"] == phase and (report is None or event["report"] == str(report))
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self.path.with_suffix(".lock").open("a") as stream:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Another process owns this ledger") from error
            try:
                self.data = json.loads(self.path.read_text())
                self._validate()
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def reserve(self, phase: str, name: str, count: int, report: Path, run_ceiling: int) -> int:
        """Spend `count` before the work starts; refuse rather than exceed either ceiling."""
        report = report.resolve()
        with self._locked():
            events = self.data["events"]
            if any(event["status"] == "unknown" for event in events):
                raise ValueError("An earlier reservation has an unknown outcome")
            if self.data["stop_after_failure"] and any(event["status"] == "failed" for event in events):
                raise ValueError("Ledger failure latch is set")
            if phase not in self.data["ceilings"]:
                raise ValueError("Ledger has no ceiling for phase " + phase)
            if any(event["phase"] == phase and event["name"] == name for event in events):
                raise ValueError("A reserved attempt cannot be repeated: " + name)
            if self.spent(phase) + count > self.data["ceilings"][phase]:
                raise ValueError("Ledger ceiling exhausted for phase " + phase)
            if self.spent(phase, report) + count > run_ceiling:
                raise ValueError("Run ceiling exhausted for phase " + phase)
            events.append({"phase": phase, "name": name, "count": count, "status": "unknown", "report": str(report)})
            durable_json(self.path, self.data)
            return len(events) - 1

    def finish(self, reservation: int, passed: bool) -> None:
        with self._locked():
            event = self.data["events"][reservation]
            if event["status"] != "unknown":
                raise ValueError("Reservation already closed")
            event["status"] = "passed" if passed else "failed"
            durable_json(self.path, self.data)

    @contextmanager
    def reserved(self, phase: str, name: str, count: int, report: Path, run_ceiling: int) -> Iterator[Outcome]:
        """Reserve, run the block, then record its outcome."""
        reservation = self.reserve(phase, name, count, report, run_ceiling)
        outcome = Outcome()
        try:
            yield outcome
        except subprocess.TimeoutExpired:
            raise  # stays unknown: the work may have dispatched
        except Exception:
            self.finish(reservation, False)
            raise
        self.finish(reservation, outcome.passed)

    def events_for(self, report: Path) -> list[dict]:
        return [event for event in self.data["events"] if event["report"] == str(report.resolve())]


def open_ledger(
    output: Path, series_dir: Path | None, ceilings: dict[str, int], stop_after_failure: bool, series_ceilings: dict
) -> Ledger:
    """The run's own ledger, or the series ledger it shares with other runs."""
    if series_dir is None:
        return Ledger.open(output / "ledger.json", ceilings=ceilings, stop_after_failure=stop_after_failure)
    return Ledger.open(
        series_dir / "ledger.json",
        ceilings=series_ceilings or None,
        stop_after_failure=stop_after_failure if series_ceilings else None,
    )


def parse_ceilings(values: list[str] | None) -> dict[str, int]:
    """`--series-ceiling authoring=4` → {"authoring": 4}."""
    ceilings = {}
    for value in values or []:
        phase, _, number = value.partition("=")
        if not phase or not number.isdigit() or phase in ceilings:
            raise ValueError("Series ceilings take the form PHASE=N, once per phase")
        ceilings[phase] = int(number)
    return ceilings
