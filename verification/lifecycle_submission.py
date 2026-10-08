"""Plan lifecycle observations and judge their immutable evidence with explicit acceptance."""

from __future__ import annotations

from collections.abc import Callable
import copy
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import Recorded, require
    from .lifecycle import digest_native, native_lifecycle, verify_lifecycle
else:
    from contracts import Recorded, require
    from lifecycle import digest_native, native_lifecycle, verify_lifecycle

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


def validate_task(config: dict) -> None:
    """The task's fixed identity and policy; the controller validates the profile."""
    workflow = config["workflow"]
    require(workflow["id"] == "daily-digest" and workflow["revision"] == 1, "Wrong lifecycle task identity")
    require(config["lifecycle"]["on_test_fail"]["max_rebuilds"] == 2, "Lifecycle task requires two rebuilds maximum")
    require(config["execution"]["deadline_seconds"] == 120, "Lifecycle task deadline changed")
    require(config["activation"]["schedule"] == "0 9 * * *", "Original daily schedule changed")
    require(config["activation"]["timezone"] == "Asia/Dubai", "Original daily timezone changed")
    require(config["activation"]["rule_id"] == "daily-digest-0900", "Cron rule identity changed")
    require(config["lifecycle"]["test"]["rule_id"] == "digest-test-requested", "Callback rule identity changed")
    require(
        workflow["inputs"]
        == {
            "articles": [
                {"id": "a1", "title": "Warehouse opens", "text": "A new warehouse opened in Dubai."},
                {"id": "a2", "title": "New payment option", "text": "The shop added a payment option."},
            ]
        },
        "Submission changed the public articles",
    )
    require(
        sorted(step["uses"] for step in workflow["steps"]) == ["digest.prepare", "digest.preview", "digest.summarize"],
        "Lifecycle task requires the three registered digest operations",
    )


def plan_digest_lifecycle(config: dict, cases: dict) -> list[dict]:
    validate_task(config)
    return plan_lifecycle(
        config,
        cases,
        tick="2026-10-04T05:00:00+00:00",
        wrong_output={"mode": "preview", "text": "Unrelated fixed digest", "article_ids": ["wrong"]},
    )


def evaluate_digest_lifecycle(entries, recorded, rows):
    evaluate_lifecycle(entries, recorded, rows, acceptance=digest_native)
