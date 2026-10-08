"""Plan lifecycle observations and judge their immutable evidence with explicit acceptance."""

from __future__ import annotations

from collections.abc import Callable
import copy
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Recorded, require
    from .lifecycle import native_lifecycle, verify_lifecycle
else:
    from contracts import Recorded, require
    from lifecycle import native_lifecycle, verify_lifecycle

MUTATION = "intentional-output-mutation"


def plan_lifecycle(config: dict, cases: dict, *, tick: str, wrong_output: dict) -> list[dict]:
    entries = []
    for case in cases["positive"]:
        candidate = copy.deepcopy(config)
        candidate["workflow"]["inputs"] = case["inputs"]
        entries.append(
            {
                "name": case["name"],
                "kind": "positive",
                "procedure": "lifecycle",
                "config": candidate,
                "callback": "stub-test",
                "tick": tick,
            }
        )
    # Engine-successful wrong output must be a rejected business result.
    broken = copy.deepcopy(config)
    broken["workflow"]["inputs"] = cases["positive"][-1]["inputs"]
    broken["workflow"]["output"] = copy.deepcopy(wrong_output)
    entries.append(
        {
            "name": MUTATION,
            "kind": "mutated-generated-workflow",
            "procedure": "lifecycle",
            "config": broken,
            "callback": "wrong-output",
            "tick": None,
        }
    )
    return entries


def evaluate_lifecycle(
    entries: list[dict], recorded: Recorded, rows: list[dict], *, acceptance: Callable[..., dict]
) -> None:
    """Judge each recorded lifecycle entry, appending its row; the first rejection raises."""
    for entry in entries:
        directory = recorded.case(entry["name"])
        row: dict = {"name": entry["name"], "passed": False}
        rows.append(row)
        passed = False
        reason = None
        try:
            snapshot = json.loads((directory / "snapshot.json").read_text())
            event = json.loads((directory / "event.json").read_text())
            if entry["kind"] == "positive":
                require(event["state"] == "passed", "Authored candidate failed its native Callback test")
                checked = verify_lifecycle(snapshot, acceptance=acceptance, mode="stub", require_wall_clock=False)
                require(len(checked["native_executions"]) == 2, "Stub gate needs both Callback and Cron")
                row.update(checked)
                passed = True
            else:
                require(event["record"]["status"] == "success", "Negative control did not succeed natively")
                native_lifecycle(entry["config"], event["record"], event["admission"], mode="stub")
                checked = acceptance(entry["config"], event["record"], event["admission"], mode="stub")
                require(checked["passed"] is False and event["state"] == "failed", "Wrong output was accepted")
                require(
                    snapshot["families"][entry["config"]["workflow"]["id"]]["active"] is None,
                    "Rejected candidate was released",
                )
                row["business_rejected"] = True
                reason = "Expected business rejection of a deliberately wrong output"
        except Exception as error:
            reason = str(error)
            raise
        finally:
            recorded.accept(entry["name"], passed, reason)
        row["passed"] = True
