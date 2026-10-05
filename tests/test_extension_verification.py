"""Synthetic native-record tests; these tests do not claim actual n8n execution."""

import copy
from pathlib import Path
import unittest

import yaml

from verification.contracts import Rejected
from verification.extensions import check_refinement_history, review_reply, verify_refinement


def evidence(texts, *, mode="live", maximum=3, limit=280):
    """Construct evidence independently, without compiler/controller/operation code."""
    config = yaml.safe_load((Path(__file__).parents[1] / "configs/04-revise-answer.yaml").read_text())
    config["execution"]["refinement"]["max_attempts"] = maximum
    config["workflow"]["inputs"]["max_characters"] = limit
    run = {
        "workflow_id": "native-workflow",
        "execution_id": "73",
        "n8n_version": "2.41.5",
        "import_exit_code": 0,
        "execute_exit_code": 0,
        "status": "success",
        "persisted_status": "success",
        "result_node_present": True,
        "run_data": {},
        "mapping": {},
    }
    for number in range(1, maximum + 1):
        run["mapping"][f"attempt{number}:draft"] = f"Attempt {number} / draft [LLM {mode.upper()}]"
        run["mapping"][f"attempt{number}:check"] = f"Attempt {number} / check"

    def add(name, value, parent=None, channel=0, branch=None, error=None):
        channels = [[{"json": copy.deepcopy(value)}]]
        if branch is not None:
            channels = [[], []]
            channels[0 if branch else 1] = [{"json": copy.deepcopy(value)}]
        record = {"startTime": len(run["run_data"]) * 10, "executionTime": 1, "data": {"main": channels}}
        if parent:
            record["source"] = [{"previousNode": parent, "previousNodeOutput": channel, "previousNodeRun": 0}]
        if error:
            record["error"] = {"message": error}
            record["data"] = {}
        run["run_data"][name] = [record]

    add("Demo start", {})
    history = {"max_attempts": maximum, "attempts": [], "accepted_attempt": None}
    runtime = {"previous": None, "feedback": []}
    for number, text in enumerate(texts, 1):
        prefix = f"Attempt {number} / "
        fixture = "Fixture" if number == 1 else prefix + "Fixture"
        state = {
            "inputs": config["workflow"]["inputs"],
            "runtime": copy.deepcopy(runtime),
            "steps": {},
            "statuses": {},
            "events": {},
            "llm_mode": mode,
            "deadline_at_ms": 90000,
            "refinement": copy.deepcopy(history),
        }
        add(fixture, state, "Demo start" if number == 1 else f"Continue {number - 1}")
        draft = {"text": text}
        errors = ([] if "A7842" in text else ["Include the order ID"]) + (
            [] if len(text.encode("utf-16-le")) // 2 <= limit else ["Shorten the reply"]
        )
        check = {"pass": not errors, "errors": errors}
        event = {
            "step_id": "draft",
            "operation": "reply.generate",
            "actor": None,
            "status": "completed",
            "implementation": mode,
        }
        draft_node = run["mapping"][f"attempt{number}:draft"]
        if mode == "live":
            invocation = f"revise-answer/r1/draft/attempt{number}/native-workflow/73"
            event["invocation_id"] = invocation
            request = {
                "invocation_id": invocation,
                "operation": "reply.generate",
                "actor": None,
                "inputs": {"ticket": state["inputs"]["ticket"], **copy.deepcopy(runtime)},
            }
            prepared = {**copy.deepcopy(state), "request": request, "should_run": True}
            add(prefix + "Prepare draft", prepared, fixture)
            add(prefix + "Guard draft", prepared, prefix + "Prepare draft", branch=True)
            add(
                prefix + "Agency draft",
                {"status": "completed", "invocation_id": invocation, "output": draft},
                prefix + "Guard draft",
            )
            parent = prefix + "Agency draft"
        else:
            parent = fixture
        state["steps"]["draft"] = draft
        state["statuses"]["draft"] = "completed"
        state["events"]["draft"] = event
        add(draft_node, state, parent)
        state["steps"]["check"] = check
        state["statuses"]["check"] = "completed"
        state["events"]["check"] = {
            "step_id": "check",
            "operation": "reply.check",
            "actor": None,
            "status": "completed",
            "implementation": "script",
        }
        check_node = run["mapping"][f"attempt{number}:check"]
        add(check_node, state, draft_node)
        attempt = {
            "number": number,
            "runtime": copy.deepcopy(runtime),
            "output": draft,
            "steps": copy.deepcopy(state["steps"]),
            "statuses": copy.deepcopy(state["statuses"]),
            "trace": list(copy.deepcopy(state["events"]).values()),
            "accepted": not errors,
        }
        history["attempts"].append(attempt)
        if not errors:
            history["accepted_attempt"] = number
        continuing = bool(errors) and number < maximum
        if continuing:
            runtime = {"previous": draft, "feedback": errors}
        state.update(refinement=copy.deepcopy(history), runtime=copy.deepcopy(runtime), continue_refinement=continuing)
        add(f"Checkpoint {number}", state, check_node)
        if number < maximum:
            add(f"Continue {number}", state, f"Checkpoint {number}", branch=continuing)
    run["refinement"] = copy.deepcopy(history)
    parent = f"Continue {number}" if number < maximum else f"Checkpoint {number}"
    channel = 1 if number < maximum else 0
    if history["accepted_attempt"]:
        final = {
            "workflow_ref": {"id": "revise-answer", "revision": 1},
            "llm_mode": mode,
            "spec_revision": "06ddd3333109cea8a2cb3071609070d7a3c0d3ff",
            "refinement": copy.deepcopy(history),
            **{key: copy.deepcopy(attempt[key]) for key in ("output", "steps", "statuses", "trace")},
        }
        add("Result", final, parent, channel)
        run.update(result=copy.deepcopy(final), output=copy.deepcopy(final["output"]))
    else:
        add("Result", None, parent, channel, error="Refinement exhausted without an accepted result")
        run.update(status="error", persisted_status="error", execute_exit_code=1, result_node_present=True, output=None)
    return config, run


class ExtensionVerificationTests(unittest.TestCase):
    def test_failed_result_node_presence_does_not_mean_a_successful_output(self):
        config, run = evidence(["Checking."] * 3)
        self.assertTrue(run["result_node_present"])
        self.assertEqual(run["run_data"]["Result"][0]["data"], {})
        self.assertTrue(verify_refinement(config, run)["exhausted"])
        run["result"] = {"output": {"text": "unaccepted"}}
        with self.assertRaises(Rejected):
            verify_refinement(config, run)

    def test_first_or_later_acceptance_and_native_exhaustion(self):
        for texts, expected in ((["Order A7842."], 1), (["Checking.", "Order A7842."], 2), (["Checking."] * 3, None)):
            with self.subTest(texts=texts):
                config, run = evidence(texts)
                result = verify_refinement(config, run)
                self.assertEqual(result["accepted_attempt"], expected)
                self.assertEqual(len(result["calls"]), len(texts))

    def test_stub_evidence_does_not_claim_live_calls(self):
        config, run = evidence(["Checking.", "Order A7842."], mode="stub")
        self.assertEqual(verify_refinement(config, run, mode="stub")["calls"], [])

    def test_length_rule_counts_utf16_units(self):
        self.assertEqual(review_reply("A😀", "A", 2), {"pass": False, "errors": ["Shorten the reply"]})

    def test_coherent_but_false_review_is_rejected_independently(self):
        config, run = evidence(["Checking.", "Order A7842."])
        attempts = copy.deepcopy(run["refinement"]["attempts"])
        attempts[0]["steps"]["check"] = {"pass": True, "errors": []}
        attempts[0]["accepted"] = True
        with self.assertRaises(Rejected):
            check_refinement_history(config, attempts[:1])

    def test_history_without_native_attempt_is_rejected(self):
        config, run = evidence(["Checking.", "Order A7842."])
        run["run_data"].pop("Attempt 2 / draft [LLM LIVE]")
        with self.assertRaises(Rejected):
            verify_refinement(config, run)

    def test_native_evidence_corruptions_fail(self):
        def value(run, name):
            return run["run_data"][name][0]["data"]["main"][0][0]["json"]

        mutations = {
            "wrong-carry": lambda r: value(r, "Attempt 2 / Fixture")["runtime"].update(feedback=[]),
            "wrong-request": lambda r: value(r, "Attempt 2 / Prepare draft")["request"]["inputs"].update(previous=None),
            "wrong-identity": lambda r: value(r, "Attempt 2 / Agency draft").update(invocation_id="different"),
            "reset-deadline": lambda r: value(r, "Attempt 2 / Fixture").update(deadline_at_ms=100000),
            "wrong-output": lambda r: value(r, "Result").update(output={"text": "unaccepted"}),
            "extra-native-call": lambda r: r["run_data"].update(
                {"Attempt 3 / Agency draft": r["run_data"]["Attempt 2 / Agency draft"]}
            ),
            "wrong-edge": lambda r: r["run_data"]["Attempt 2 / Fixture"][0]["source"][0].update(previousNodeOutput=1),
            "adapter-only-pass": lambda r: r.update(output={"text": "changed"}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                config, run = evidence(["Checking.", "Order A7842."])
                mutate(run)
                with self.assertRaises(Rejected):
                    verify_refinement(config, run)

    def test_execution_after_acceptance_and_premature_stop_rejected(self):
        for texts in (["Order A7842.", "Order A7842."], ["Checking."]):
            with self.subTest(texts=texts):
                config, run = evidence(texts)
                with self.assertRaises(Rejected):
                    verify_refinement(config, run)


if __name__ == "__main__":
    unittest.main()
