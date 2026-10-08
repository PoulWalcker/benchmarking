"""Durable candidate lifecycle: registry, event admission, test, release and rebuild around native executions.

None of this lives in the exported graph; only admitted Callback/Cron events execute it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
import copy
from datetime import UTC, datetime
import fcntl
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
from zoneinfo import ZoneInfo

from sapi_config_lab import profile
from sapi_config_lab.contracts import Document, ExecutionRecord, LlmMode, WorkflowBackend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.evidence import canonical, digest, durable_json
from sapi_config_lab.execute.agency import WRAPPER_TIMEOUT_SECONDS
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.paths import CATALOG

Verifier = Callable[[Document, ExecutionRecord], Document]
Rebuilder = Callable[[Document, Document, Document, Path], Document]


def ref_key(ref: Document) -> str:
    profile.check(set(ref) == {"id", "revision"}, "Invalid workflow reference")
    profile.check(
        isinstance(ref["id"], str) and type(ref["revision"]) is int and ref["revision"] > 0,
        "Invalid workflow reference",
    )
    return f"{ref['id']}@{ref['revision']}"


def daily_schedule(schedule: str, zone: str) -> tuple[int, int, ZoneInfo]:
    """The supported Cron subset is one fixed local minute daily, no catch-up."""
    fields = schedule.split()
    profile.check(len(fields) == 5 and fields[2:] == ["*", "*", "*"], "Only daily five-field Cron is supported")
    profile.check(fields[0].isdigit() and fields[1].isdigit(), "Daily Cron requires a fixed minute/hour")
    minute, hour = int(fields[0]), int(fields[1])
    profile.check(0 <= minute < 60 and 0 <= hour < 24, "Cron minute/hour out of range")
    try:
        timezone_value = ZoneInfo(zone)
    except (ValueError, KeyError) as error:
        raise profile.Invalid("Unknown Cron timezone") from error
    return minute, hour, timezone_value


def validate_lifecycle(config: Document, bindings: Document | None = None) -> None:
    profile.validate(config, profile.read_bindings(CATALOG) if bindings is None else bindings)
    policy = config.get("lifecycle")
    profile.check(isinstance(policy, dict), "Lifecycle policy is required")
    assert isinstance(policy, dict)
    expected: Document = {
        "test": {
            "initiator": "Callback",
            "condition": "test_environment_ready",
            "reaction": "Trigger",
            "output_mode": "preview",
        },
        "on_test_pass": {"action": "release_candidate", "retarget": "exact_tested_revision"},
        "on_test_fail": {
            "action": "wbs_rebuild",
            "source_work": "current_candidate",
            "output": "new_workflow_fork",
            "archive_original": "after_fork_persisted",
            "next": "request_test_via_callback",
            "exhausted": "suspend_and_create_adhoc",
        },
        "persistence": {
            "store": "workflow_registry",
            "immutable_definitions": True,
            "record_test_results": True,
            "deduplicate_by": ["rule_id", "event_id"],
        },
        "scheduling": {"overlap": "skip_if_running", "missed_run": "skip", "suspended": "do_not_start"},
        "replacement": {
            "in_flight": "finish_pinned_revision",
            "activate_new_revision": "after_previous_run_finishes",
            "restore_archive": "restore_definition_without_replay",
        },
    }
    for section, fields in expected.items():
        for field, value in fields.items():
            profile.check(policy[section][field] == value, f"Unsupported lifecycle policy: {section}.{field}")
    profile.check(policy["on_test_fail"]["max_rebuilds"] <= 10, "Lifecycle supports at most ten rebuilds")
    daily_schedule(config["activation"]["schedule"], config["activation"]["timezone"])


def check_fork(candidate: Document, source: Document, target: Document) -> None:
    profile.check(candidate["activation"]["workflow_ref"] == target, "Builder returned another revision")
    for field in ("schema", "spec_revision", "lifecycle", "execution"):
        profile.check(candidate[field] == source[field], f"Builder changed frozen {field}")
    for field in ("inputs", "acceptance"):
        profile.check(
            candidate["workflow"][field] == source["workflow"][field], f"Builder changed frozen workflow.{field}"
        )
    profile.check(
        {k: v for k, v in candidate["activation"].items() if k != "workflow_ref"}
        == {k: v for k, v in source["activation"].items() if k != "workflow_ref"},
        "Builder changed activation policy",
    )


class LifecycleController:
    def __init__(
        self,
        registry_dir: Path,
        *,
        backend: WorkflowBackend | None = None,
        verifier: Verifier | None = None,
        rebuilder: Rebuilder | None = None,
        llm_mode: LlmMode = "stub",
        bridge_url: str | None = None,
        bindings: Document | None = None,
    ):
        self.directory = Path(registry_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "registry.sqlite3"
        self.backend, self.verifier, self.rebuilder = backend, verifier, rebuilder
        self.llm_mode, self.bridge_url = llm_mode, bridge_url
        self.bindings = profile.read_bindings(CATALOG) if bindings is None else bindings
        initial = {
            "schema": "sapi-lab-lifecycle/v1",
            "definitions": {},
            "families": {},
            "events": {},
            "rebuilds": {},
            "transitions": [],
            "adhoc": [],
        }
        with closing(self._connection()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS registry (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)"
            )
            connection.execute("INSERT OR IGNORE INTO registry VALUES (1, ?)", (canonical(initial),))

    def _connection(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[Document]:
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = json.loads(connection.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
            self._validate_state(state)
            yield state
            self._validate_state(state)
            connection.execute("UPDATE registry SET payload=? WHERE id=1", (canonical(state),))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_state(state):
        profile.check(state.get("schema") == "sapi-lab-lifecycle/v1", "Unknown registry schema")
        for key, definition in state["definitions"].items():
            profile.check(
                key == ref_key(definition["workflow_ref"]) and digest(definition["config"]) == definition["sha256"],
                "Immutable definition changed",
            )
        for family_id, family in state["families"].items():
            rebuilds = [r for r in state["rebuilds"].values() if r["target"]["id"] == family_id]
            profile.check(family["rebuild_count"] == len(rebuilds), "Rebuild counter differs from reserved history")
            profile.check(
                sorted(r["number"] for r in rebuilds) == list(range(1, len(rebuilds) + 1)), "Rebuild sequence changed"
            )
            current = state["definitions"][family["current"]]
            profile.check(
                len(rebuilds) <= current["config"]["lifecycle"]["on_test_fail"]["max_rebuilds"],
                "Rebuild limit exceeded",
            )
            for field in ("active", "pending"):
                profile.check(
                    family[field] is None or family[field] in state["definitions"], "Unknown release revision"
                )

    @staticmethod
    def _transition(state, kind, data):
        state["transitions"].append(
            {"sequence": len(state["transitions"]) + 1, "kind": kind, "at": time.time(), "data": copy.deepcopy(data)}
        )

    def snapshot(self) -> Document:
        with closing(self._connection()) as connection:
            state = json.loads(connection.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
        self._validate_state(state)
        return state

    def register(
        self, config: Document, *, schedule_overlay: Document | None = None, forked_from: Document | None = None
    ) -> Document:
        config = copy.deepcopy(config)
        validate_lifecycle(config, self.bindings)
        profile.check(callable(self.verifier), "Lifecycle requires an explicit acceptance callable")
        ref = config["activation"]["workflow_ref"]
        key, family_id = ref_key(ref), ref["id"]
        schedule = {"schedule": config["activation"]["schedule"], "timezone": config["activation"]["timezone"]}
        if schedule_overlay is not None:
            profile.check(set(schedule_overlay) == {"schedule", "timezone"}, "Invalid schedule overlay")
            daily_schedule(schedule_overlay["schedule"], schedule_overlay["timezone"])
            schedule = copy.deepcopy(schedule_overlay)
        with self._transaction() as state:
            if key in state["definitions"]:
                profile.check(state["definitions"][key]["sha256"] == digest(config), "Cannot rewrite a definition")
                profile.check(state["families"][family_id]["schedule"] == schedule, "Cannot change frozen schedule")
                return copy.deepcopy(ref)
            if family_id in state["families"]:
                profile.check(forked_from is not None, "New revisions must be created as forks")
                assert forked_from is not None
                parent = ref_key(forked_from)
                family = state["families"][family_id]
                profile.check(
                    parent == family["current"] and ref["revision"] > forked_from["revision"],
                    "Fork must replace the current candidate with a new revision",
                )
                profile.check(not family["suspended"], "Lifecycle is suspended")
                check_fork(config, state["definitions"][parent]["config"], ref)
                if schedule_overlay is not None:
                    profile.check(schedule_overlay == family["schedule_overlay"], "Cannot change frozen schedule")
                state["definitions"][key] = {
                    "workflow_ref": ref,
                    "config": config,
                    "sha256": digest(config),
                    "forked_from": parent,
                    "status": "draft",
                }
                self._transition(
                    state,
                    "fork_persisted",
                    {
                        "origin": "registered_candidate",
                        "definition": key,
                        "sha256": digest(config),
                        "forked_from": parent,
                    },
                )
                state["definitions"][parent]["status"] = "archived"
                self._transition(state, "archived", {"definition": parent, "replacement": key})
                family["current"] = key
                if family["active"] == parent:
                    family["active"] = None
                return copy.deepcopy(ref)
            profile.check(forked_from is None, "Unknown fork parent")
            state["definitions"][key] = {
                "workflow_ref": ref,
                "config": config,
                "sha256": digest(config),
                "forked_from": None,
                "status": "draft",
            }
            state["families"][family_id] = {
                "current": key,
                "active": None,
                "pending": None,
                "cron_event": None,
                "suspended": False,
                "rebuild_count": 0,
                "schedule": schedule,
                "schedule_overlay": schedule_overlay,
            }
            self._transition(
                state, "registered", {"definition": key, "sha256": digest(config), "schedule_overlay": schedule_overlay}
            )
        return copy.deepcopy(ref)

    def _admit(self, ref, kind, event_id, condition=True, *, clock_source=None, scheduled_at=None):
        key = ref_key(ref)
        with self._transaction() as state:
            profile.check(key in state["definitions"], "Unknown workflow revision")
            definition = state["definitions"][key]
            config = definition["config"]
            family = state["families"][ref["id"]]
            rule = config["lifecycle"]["test"]["rule_id"] if kind == "Callback" else config["activation"]["rule_id"]
            profile.check(isinstance(event_id, str) and bool(event_id), "Event ID is required")
            event_key = digest({"rule_id": rule, "event_id": event_id})
            admission = {
                "kind": kind,
                "rule_id": rule,
                "event_id": event_id,
                "workflow_ref": ref,
                "purpose": "test" if kind == "Callback" else "scheduled",
            }
            if event_key in state["events"]:
                existing = state["events"][event_key]
                profile.check(
                    existing["admission"] == admission and existing["condition"] == condition,
                    "Event identity reused with different payload",
                )
                return event_key
            profile.check(not family["suspended"], "Lifecycle is suspended")
            profile.check(definition["status"] != "archived", "Archived definition cannot execute")
            if kind == "Callback" and condition:
                profile.check(
                    not any(
                        e["definition"] == key and e["admission"]["kind"] == "Callback" and e["condition"]
                        for e in state["events"].values()
                    ),
                    "Candidate already has a test event",
                )
            if kind == "Cron":
                profile.check(family["active"] == key, "Cron may execute only the released revision")
            event_state = "queued" if condition else "dismissed"
            if kind == "Cron" and family["cron_event"] is not None:
                event_state = "skipped_overlap"
            state["events"][event_key] = {
                "admission": admission,
                "definition": key,
                "condition": condition,
                "state": event_state,
                "artifact_dir": str(self.directory / "events" / event_key),
            }
            if kind == "Cron":
                state["events"][event_key].update(clock_source=clock_source, scheduled_at=scheduled_at)
            self._transition(
                state, "event_admitted", {"event": event_key, "state": event_state, "admission": admission}
            )
            return event_key

    def callback(self, ref: Document, event_id: str, *, condition: bool = True) -> Document:
        profile.check(type(condition) is bool, "Callback condition must be boolean")
        event_key = self._admit(ref, "Callback", event_id, condition)
        self.drain()
        return self.snapshot()["events"][event_key]

    def _execute_event(self, event_key):
        with self._transaction() as state:
            event = state["events"][event_key]
            if event["state"] != "queued":
                return

            config = copy.deepcopy(state["definitions"][event["definition"]]["config"])
            family = state["families"][event["admission"]["workflow_ref"]["id"]]
            profile.check(not family["suspended"], "Lifecycle is suspended")
            if (
                event["admission"]["kind"] == "Cron"
                and event.get("clock_source") == "system"
                and time.time() >= event["scheduled_at"] + 60
            ):
                event["state"] = "skipped_missed"
                self._transition(state, "missed_run_skipped", {"event": event_key})
                return
            event["state"] = "running"
            event["deadline_at"] = time.time() + config["execution"]["deadline_seconds"]
            if event["admission"]["kind"] == "Cron":
                family["cron_event"] = event_key
            self._transition(
                state,
                "execution_reserved",
                {"event": event_key, "definition": event["definition"], "deadline_at": event["deadline_at"]},
            )
            admitted = copy.deepcopy(event)
        record = run_case(
            config,
            Path(admitted["artifact_dir"]),
            backend=self.backend,
            bindings=self.bindings,
            llm_mode=self.llm_mode,
            bridge_url=self.bridge_url,
            admission=admitted["admission"],
            deadline_at=admitted["deadline_at"],
        )
        durable_json(Path(admitted["artifact_dir"]) / "native-record.json", record)
        with self._transaction() as state:
            event = state["events"][event_key]
            event["record"] = record
            event["state"] = "native_complete"
            self._transition(state, "native_complete", {"event": event_key, "status": record["status"]})
            if record.get("execute_exit_code") == 124 or record["status"] in (
                "engine_error",
                "compile_error",
                "import_error",
            ):
                event["state"] = "unknown"
                self._suspend(state, event["admission"]["workflow_ref"]["id"], "Uncertain native execution", event_key)
                return
        self._finish_event(event_key)

    def _finish_event(self, event_key):
        state = self.snapshot()
        event = state["events"][event_key]
        config = state["definitions"][event["definition"]]["config"]
        profile.check(callable(self.verifier), "Lifecycle requires an explicit acceptance callable")
        assert self.verifier is not None
        decision = self.verifier(config, event["record"])
        profile.check(
            decision.get("verifier") == config["lifecycle"]["test"]["verifier"]
            and type(decision.get("passed")) is bool
            and isinstance(decision.get("findings"), list)
            and bool(decision["findings"]) != decision["passed"],
            "Invalid operational verifier result",
        )
        with self._transaction() as state:
            event = state["events"][event_key]
            profile.check(event["state"] == "native_complete", "Event already finalized")
            definition = state["definitions"][event["definition"]]
            family = state["families"][event["admission"]["workflow_ref"]["id"]]
            event["decision"], event["state"] = decision, "passed" if decision["passed"] else "failed"
            self._transition(
                state,
                "test_result" if event["admission"]["kind"] == "Callback" else "cron_result",
                {"event": event_key, "definition": event["definition"], "decision": decision},
            )
            if event["admission"]["kind"] == "Callback" and decision["passed"]:
                definition["status"] = "tested"
                if family["cron_event"] is None:
                    family["active"] = event["definition"]
                    definition["status"] = "released"
                    self._transition(state, "released", {"definition": event["definition"], "test_event": event_key})
                else:
                    family["pending"] = event["definition"]
                    self._transition(
                        state, "release_pending", {"definition": event["definition"], "test_event": event_key}
                    )
            elif event["admission"]["kind"] == "Callback":
                definition["status"] = "rejected"
                limit = config["lifecycle"]["on_test_fail"]["max_rebuilds"]
                family_id = event["admission"]["workflow_ref"]["id"]
                if family["rebuild_count"] >= limit:
                    self._suspend(state, family_id, "Rebuild limit exhausted", event_key)
                else:
                    family["rebuild_count"] += 1
                    target = {
                        "id": family_id,
                        "revision": 1
                        + max(
                            d["workflow_ref"]["revision"]
                            for d in state["definitions"].values()
                            if d["workflow_ref"]["id"] == family_id
                        ),
                    }
                    rebuild_key = digest({"event": event_key, "target": target})
                    state["rebuilds"][rebuild_key] = {
                        "state": "queued",
                        "number": family["rebuild_count"],
                        "source": event["definition"],
                        "target": target,
                        "test_event": event_key,
                        "findings": decision,
                        "artifact_dir": str(self.directory / "rebuilds" / rebuild_key),
                    }
                    self._transition(
                        state,
                        "rebuild_queued",
                        {"rebuild": rebuild_key, "source": event["definition"], "target": target},
                    )
            if event["admission"]["kind"] == "Cron":
                family["cron_event"] = None
                if family["pending"] is not None:
                    family["active"] = family["pending"]
                    family["pending"] = None
                    state["definitions"][family["active"]]["status"] = "released"
                    self._transition(
                        state, "replacement_activated", {"definition": family["active"], "finished_event": event_key}
                    )

    def _suspend(self, state, family_id, reason, cause):
        family = state["families"][family_id]
        family["suspended"], family["active"], family["pending"] = True, None, None
        state["adhoc"].append({"family": family_id, "reason": reason, "cause": cause, "state": "pending"})
        self._transition(state, "suspended", {"family": family_id, "reason": reason, "cause": cause})

    def _install_fork(self, rebuild_key, candidate):
        validate_lifecycle(candidate, self.bindings)
        with self._transaction() as state:
            rebuild = state["rebuilds"][rebuild_key]
            profile.check(rebuild["state"] == "running", "Rebuild already resolved")
            source = state["definitions"][rebuild["source"]]["config"]
            target = rebuild["target"]
            check_fork(candidate, source, target)
            key = ref_key(target)
            profile.check(key not in state["definitions"], "Fork revision already exists")
            state["definitions"][key] = {
                "workflow_ref": copy.deepcopy(target),
                "config": copy.deepcopy(candidate),
                "sha256": digest(candidate),
                "forked_from": rebuild["source"],
                "status": "draft",
            }
            self._transition(
                state,
                "fork_persisted",
                {
                    "rebuild": rebuild_key,
                    "definition": key,
                    "sha256": digest(candidate),
                    "forked_from": rebuild["source"],
                },
            )
            state["definitions"][rebuild["source"]]["status"] = "archived"
            self._transition(state, "archived", {"definition": rebuild["source"], "replacement": key})
            family = state["families"][target["id"]]
            family["current"] = key
            if family["active"] == rebuild["source"]:
                family["active"] = None
            rebuild["state"], rebuild["candidate_sha256"] = "completed", digest(candidate)
            # Persist the next Callback in the same transaction as its new fork.
            admission = {
                "kind": "Callback",
                "rule_id": candidate["lifecycle"]["test"]["rule_id"],
                "event_id": "rebuild-" + rebuild_key,
                "workflow_ref": target,
                "purpose": "test",
            }
            event_key = digest({"rule_id": admission["rule_id"], "event_id": admission["event_id"]})
            state["events"][event_key] = {
                "admission": admission,
                "definition": key,
                "condition": True,
                "state": "queued",
                "artifact_dir": str(self.directory / "events" / event_key),
            }
            self._transition(state, "event_admitted", {"event": event_key, "state": "queued", "admission": admission})

    def _run_rebuild(self, rebuild_key):
        if self.rebuilder is None:
            return False
        with self._transaction() as state:
            rebuild = state["rebuilds"][rebuild_key]
            profile.check(rebuild["state"] == "queued", "Rebuild is not queued")
            rebuild["state"] = "running"
            rebuild["reserved_at"] = time.time()
            source = copy.deepcopy(state["definitions"][rebuild["source"]]["config"])
            request = copy.deepcopy(rebuild)
            self._transition(
                state,
                "rebuild_reserved",
                {"rebuild": rebuild_key, "number": rebuild["number"], "target": rebuild["target"]},
            )
        try:
            directory = Path(request["artifact_dir"])
            directory.mkdir(parents=True, exist_ok=True)
            candidate = self.rebuilder(source, request["findings"], request["target"], directory)
            durable_json(directory / "rebuild-result.json", {"rebuild": rebuild_key, "candidate": candidate})
            self._install_fork(rebuild_key, candidate)
        except Exception as error:  # An unknown rebuild outcome must suspend, fail closed
            with self._transaction() as state:
                rebuild = state["rebuilds"][rebuild_key]
                rebuild["state"], rebuild["error_type"] = "unknown", type(error).__name__
                self._suspend(state, rebuild["target"]["id"], "Rebuild outcome rejected or uncertain", rebuild_key)
        return True

    def tick(self, now: datetime | None = None) -> list[Document]:
        """Admit the current daily wall-clock minute only; never replay missed days."""
        clock_source = "injected" if now is not None else "system"
        now = now or datetime.now(UTC)
        profile.check(now.tzinfo is not None, "Cron time must include a timezone")
        state = self.snapshot()
        keys = []
        for family in state["families"].values():
            if family["suspended"] or family["active"] is None:
                continue
            minute, hour, zone = daily_schedule(family["schedule"]["schedule"], family["schedule"]["timezone"])
            local = now.astimezone(zone)
            if (local.hour, local.minute) != (hour, minute):
                continue
            definition = state["definitions"][family["active"]]
            event_id = local.strftime("%Y-%m-%dT%H:%M") + "@" + str(zone)
            rule = definition["config"]["activation"]["rule_id"]
            event_key = digest({"rule_id": rule, "event_id": event_id})
            if event_key not in state["events"]:
                event_key = self._admit(
                    definition["workflow_ref"],
                    "Cron",
                    event_id,
                    clock_source=clock_source,
                    scheduled_at=now.replace(second=0, microsecond=0).timestamp(),
                )
            keys.append(event_key)
        self.drain()
        return [self.snapshot()["events"][key] for key in keys]

    def restore(self, ref: Document) -> Document:
        """Restore only an archived definition, with no replay or schedule release."""
        key = ref_key(ref)
        with self._transaction() as state:
            profile.check(key in state["definitions"], "Unknown definition")
            definition = state["definitions"][key]
            profile.check(definition["status"] == "archived", "Only archived definitions can be restored")
            definition["status"] = "draft"
            self._transition(state, "restored", {"definition": key, "replayed": False})
            return copy.deepcopy(definition)

    def _recover(self):
        """Under the runner lock: finish durable results; suspend a family whose result is missing or partial."""
        for event_key, event in self.snapshot()["events"].items():
            if event["state"] == "native_complete":
                self._finish_event(event_key)
            elif event["state"] == "running":
                try:
                    record = json.loads((Path(event["artifact_dir"]) / "native-record.json").read_text())
                    config = json.loads((Path(event["artifact_dir"]) / "config.json").read_text())
                    definition = self.snapshot()["definitions"][event["definition"]]
                    profile.check(
                        digest(config) == definition["sha256"] and record["input"]["activation"] == event["admission"],
                        "Recovered result identity mismatch",
                    )
                    profile.check(
                        record["status"] != "engine_error" and record.get("execute_exit_code") != 124,
                        "Uncertain native execution",
                    )
                except OSError, ValueError, KeyError:
                    with self._transaction() as state:
                        state["events"][event_key]["state"] = "unknown"
                        self._suspend(
                            state,
                            event["admission"]["workflow_ref"]["id"],
                            "Uncertain native execution after restart",
                            event_key,
                        )
                else:
                    with self._transaction() as state:
                        state["events"][event_key].update(state="native_complete", record=record)
                        self._transition(state, "native_recovered", {"event": event_key})
                    self._finish_event(event_key)
        for rebuild_key, rebuild in self.snapshot()["rebuilds"].items():
            if rebuild["state"] != "running":
                continue
            try:
                result = json.loads((Path(rebuild["artifact_dir"]) / "rebuild-result.json").read_text())
                profile.check(result["rebuild"] == rebuild_key, "Recovered rebuild identity mismatch")
                self._install_fork(rebuild_key, result["candidate"])
            except OSError, ValueError, KeyError:
                with self._transaction() as state:
                    state["rebuilds"][rebuild_key]["state"] = "unknown"
                    self._suspend(state, rebuild["target"]["id"], "Uncertain rebuild after restart", rebuild_key)

    def drain(self) -> None:
        profile.check(callable(self.verifier), "Lifecycle requires an explicit acceptance callable")
        with (self.directory / "runner.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self._recover()
            while True:
                state = self.snapshot()
                queued = [
                    key
                    for key, event in state["events"].items()
                    if event["state"] == "queued"
                    and not state["families"][event["admission"]["workflow_ref"]["id"]]["suspended"]
                ]
                if queued:
                    self._execute_event(queued[0])
                    continue
                rebuilds = [
                    key
                    for key, rebuild in state["rebuilds"].items()
                    if rebuild["state"] == "queued" and not state["families"][rebuild["target"]["id"]]["suspended"]
                ]
                if rebuilds and self._run_rebuild(rebuilds[0]):
                    continue
                return


def main(argv: list[str] | None = None, *, verifier: Verifier | None = None) -> int:
    host = HostConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--llm-mode", choices=("stub", "live"), default="stub")
    parser.add_argument("--bridge-url")
    parser.add_argument("--bindings", type=Path, default=CATALOG)
    parser.add_argument("--rebuilder-url", help="Explicit real wrapper URL for bounded candidate repair")
    parser.add_argument(
        "--rebuild-model",
        default=host.wrapper_model,
        help="Expected wrapper model identity (SAPI_WRAPPER_MODEL); does not select or override it",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    register.add_argument("--config", type=Path, required=True)
    register.add_argument("--schedule-overlay", help="Explicit daily test Cron; original config stays unchanged")
    register.add_argument("--timezone", help="Timezone of the schedule overlay; default: the config's own")
    for command in ("callback", "restore"):
        action = sub.add_parser(command)
        action.add_argument("--workflow-id", required=True)
        action.add_argument("--revision", type=int, required=True)
        if command == "callback":
            action.add_argument("--event-id", required=True)
            action.add_argument("--condition-false", action="store_true")
    sub.add_parser("status")
    sub.add_parser("tick")
    sub.add_parser("drain")
    serve = sub.add_parser("serve")
    serve.add_argument("--duration-seconds", type=int, required=True)
    args = parser.parse_args(argv)
    from sapi_config_lab.author.rebuilder import WrapperRebuilder

    bindings = profile.read_bindings(args.bindings)
    controller = LifecycleController(
        args.registry,
        verifier=verifier,
        bindings=bindings,
        llm_mode=args.llm_mode,
        bridge_url=args.bridge_url,
        rebuilder=WrapperRebuilder(
            args.rebuilder_url,
            timeout_seconds=WRAPPER_TIMEOUT_SECONDS,
            expected_model=args.rebuild_model,
            bindings=bindings,
        )
        if args.rebuilder_url
        else None,
    )
    result: Any
    if args.command == "register":
        config = profile.read(args.config)
        overlay = None
        if args.schedule_overlay:
            zone = args.timezone or config["activation"]["timezone"]
            overlay = {"schedule": args.schedule_overlay, "timezone": zone}
        result = controller.register(config, schedule_overlay=overlay)
    elif args.command == "callback":
        result = controller.callback(
            {"id": args.workflow_id, "revision": args.revision}, args.event_id, condition=not args.condition_false
        )
    elif args.command == "restore":
        result = controller.restore({"id": args.workflow_id, "revision": args.revision})
    elif args.command == "tick":
        result = controller.tick()
    elif args.command == "drain":
        controller.drain()
        result = controller.snapshot()
    elif args.command == "serve":
        profile.check(0 < args.duration_seconds <= 3600, "Scheduler duration must be 1..3600 seconds")
        deadline = time.monotonic() + args.duration_seconds
        ticks = 0
        try:
            while time.monotonic() < deadline:
                controller.tick()
                ticks += 1
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        except KeyboardInterrupt:
            pass
        result = {"ticks": ticks}
    else:
        result = controller.snapshot()
    snapshot = controller.snapshot()
    durable_json(controller.directory / "snapshot.json", snapshot)
    print(json.dumps({"result": result, "snapshot": str(controller.directory / "snapshot.json")}, ensure_ascii=False))
    unfinished = any(r["state"] == "queued" for r in snapshot["rebuilds"].values())
    return 1 if unfinished or any(f["suspended"] for f in snapshot["families"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
