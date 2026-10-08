"""Native Docker control for generic event, revision and rebuild semantics."""

import copy
from datetime import UTC, datetime
import json
from pathlib import Path

from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.lifecycle import LifecycleController
from sapi_config_lab.evidence import durable_json
from tests.support.lifecycle import ROOT, acceptance, bindings, config, native_acceptance
from verification.lifecycle import verify_lifecycle


def run(directory):
    backend = N8nBackend((ROOT / "operations.js").read_text())
    original = config()
    original["workflow"]["output"] = {"ref": "steps.decode.value"}

    def rebuild(source, findings, target, artifacts):
        source["workflow"]["revision"] = target["revision"]
        source["activation"]["workflow_ref"] = target
        source["workflow"]["output"] = {"ref": "steps.decode"}
        return source

    controller = LifecycleController(
        directory, backend=backend, verifier=acceptance, bindings=bindings(), rebuilder=rebuild
    )
    first = controller.register(original)
    rejected = controller.callback(first, "reject-and-rebuild")
    assert rejected["state"] == "failed"
    state = controller.snapshot()
    assert state["families"][first["id"]]["active"] == "lifecycle-fixture@2"
    execute = backend.execute
    pinned = []

    def during_cron(compiled, artifacts, binding):
        pinned.append(copy.deepcopy(binding.admission["workflow_ref"]))
        proposed = config()
        proposed["workflow"]["revision"] = 3
        proposed["activation"]["workflow_ref"]["revision"] = 3
        third = controller.register(proposed, forked_from={"id": first["id"], "revision": 2})
        assert controller.callback(third, "test-third")["state"] == "queued"
        backend.execute = execute
        return execute(compiled, artifacts, binding)

    backend.execute = during_cron
    tick = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
    controller.tick(tick)
    assert pinned == [{"id": first["id"], "revision": 2}]
    state = controller.snapshot()
    assert state["families"][first["id"]]["active"] == "lifecycle-fixture@3"
    count = len(state["events"])
    restarted = LifecycleController(directory, backend=backend, verifier=acceptance, bindings=bindings())
    assert restarted.callback(first, "reject-and-rebuild") == rejected
    restarted.drain()
    assert len(restarted.snapshot()["events"]) == count
    restored = restarted.restore(first)
    assert restored["status"] == "draft"
    state = restarted.snapshot()
    assert state["families"][first["id"]]["active"] == "lifecycle-fixture@3"
    verdict = verify_lifecycle(state, acceptance=native_acceptance, mode="stub", require_wall_clock=False)
    assert len(verdict["native_executions"]) == 4
    assert [row["accepted"] for row in verdict["native_executions"]] == [False, True, True, True]
    for event in state["events"].values():
        record = event["record"]
        assert record["acceptance"] == {"status": "not_evaluated", "passed": None}
        artifacts = Path(event["artifact_dir"])
        assert all(
            (artifacts / filename).is_file()
            for filename in ("execution.json", "execution.persisted.json", "execution.metadata.json")
        )
    durable_json(directory / "snapshot.json", state)
    durable_json(directory / "verification.json", verdict)
    print(json.dumps({"native_executions": 4, "accepted": 3, "rejected": 1, "rebuilds": 1, "paid_calls": 0}))


if __name__ == "__main__":
    run(Path("/evidence"))
