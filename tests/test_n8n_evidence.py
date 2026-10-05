"""Persisted n8n evidence retains arrays and shared object references."""

import unittest

from sapi_config_lab.runtime.n8n.execution import decode_flatted


class PersistedEvidenceTests(unittest.TestCase):
    def test_array_root_preserves_repeated_object_references(self):
        result = decode_flatted('[["1", "1"], {"value": "2"}, "shared"]')
        self.assertEqual(result, [{"value": "shared"}, {"value": "shared"}])
        self.assertIs(result[0], result[1])

    def test_object_root_decodes_empty_and_nested_arrays(self):
        result = decode_flatted('[{"items": "1", "empty": "2"}, ["3"], [], {"count": 7}]')
        self.assertEqual(result, {"items": [{"count": 7}], "empty": []})


class NativeLiveEvidenceTests(unittest.TestCase):
    def live_record(self):
        request = {
            "invocation_id": "ticket-routing/r1/classify/wf/1",
            "operation": "ticket.classify",
            "actor": None,
            "inputs": {"ticket": {"id": "T1", "text": "late", "days_overdue": 3}},
        }
        response = {
            "invocation_id": request["invocation_id"],
            "status": "completed",
            "output": {"category": "delivery", "priority": "high"},
            "usage": None,
        }
        event = {
            "step_id": "classify",
            "operation": "ticket.classify",
            "implementation": "live",
            "status": "completed",
            "actor": None,
            "invocation_id": request["invocation_id"],
        }
        prepared = {"should_run": True, "request": request}
        restored = {"events": {"classify": event}, "steps": {"classify": response["output"]}}
        final = {"trace": [event], "workflow_ref": {"id": "ticket-routing", "revision": 1}}
        data = {}
        for i, (name, value, previous) in enumerate(
            (
                ("Prepare classify", prepared, "Fixture"),
                ("Guard classify", prepared, "Prepare classify"),
                ("Agency classify", response, "Guard classify"),
                ("classify [LLM LIVE]", restored, "Agency classify"),
                ("Result", final, "classify [LLM LIVE]"),
            )
        ):
            data[name] = [
                {
                    "startTime": i * 10,
                    "executionTime": 1,
                    "source": [{"previousNode": previous, "previousNodeOutput": 0, "previousNodeRun": 0}],
                    "data": {"main": [[{"json": value}]]},
                }
            ]
        data["Guard classify"][0]["data"]["main"].append([])
        return {
            "run_data": data,
            "mapping": {"classify": "classify [LLM LIVE]"},
            "workflow_id": "wf",
            "execution_id": "1",
        }

    def test_native_live_chain_is_required_even_when_authored_trace_is_complete(self):
        import copy
        from verification.n8n_provenance import live_operations
        from verification.contracts import Rejected

        run = self.live_record()
        self.assertEqual(live_operations(run)[0]["request"]["operation"], "ticket.classify")
        for node in ("Prepare classify", "Guard classify", "Agency classify", "classify [LLM LIVE]"):
            bad = copy.deepcopy(run)
            del bad["run_data"][node]
            with self.subTest(missing=node), self.assertRaises(Rejected):
                live_operations(bad)
        for change in ("duplicate", "response", "source", "identity", "extra"):
            bad = copy.deepcopy(run)
            http = bad["run_data"]["Agency classify"][0]
            if change == "duplicate":
                bad["run_data"]["Agency classify"].append(copy.deepcopy(http))
            elif change == "response":
                http["data"]["main"][0][0]["json"]["output"] = {"category": "other", "priority": "normal"}
            elif change == "source":
                http["source"][0]["previousNodeOutput"] = 1
            elif change == "identity":
                bad["workflow_id"] = "another"
            else:
                bad["run_data"]["Agency invented"] = [copy.deepcopy(http)]
            with self.subTest(change=change), self.assertRaises(Rejected):
                live_operations(bad)

    def test_skipped_live_occurrence_requires_native_false_channel_and_no_request(self):
        import copy
        from verification.n8n_provenance import live_operations
        from verification.contracts import Rejected

        run = self.live_record()
        event = run["run_data"]["Result"][0]["data"]["main"][0][0]["json"]["trace"][0]
        event["status"] = "skipped"
        event.pop("invocation_id")
        prepared = {"should_run": False, "request": None}
        run["run_data"]["Prepare classify"][0]["data"]["main"] = [[{"json": copy.deepcopy(prepared)}]]
        guard = run["run_data"]["Guard classify"][0]
        guard["data"]["main"] = [[], [{"json": copy.deepcopy(prepared)}]]
        restore = run["run_data"]["classify [LLM LIVE]"][0]
        restore["data"]["main"][0][0]["json"] = {"events": {"classify": event}, "steps": {}}
        restore["source"] = [{"previousNode": "Guard classify", "previousNodeOutput": 1, "previousNodeRun": 0}]
        run["run_data"].pop("Agency classify")
        self.assertEqual(live_operations(run), [])
        guard["data"]["main"].reverse()
        with self.assertRaisesRegex(Rejected, "false"):
            live_operations(run)
