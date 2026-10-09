"""Checkout's native world protocol preserves oracle, receipts and frozen evidence."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import Invalid
from tests.support.checkout_controls import CHECKOUT_ORACLE_ACTIONS
from tests.support.pinned import AVAILABLE, SOURCE

DIRECTORY = workspace_root() / "tasks/checkout-recovery/environment"


def load(name):
    spec = importlib.util.spec_from_file_location("_checkout_environment_" + name, DIRECTORY / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SERVER, HOOKS = load("server"), load("hooks")


class CheckoutSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.upstream = Mock()
        self.upstream.finalize.return_value = {"state": {"effect": "committed"}}
        self.world = SERVER.World(
            self.upstream, {"limits": {"wall_clock_seconds": 60}}, evidence_path=self.root / "world"
        )
        self.addCleanup(self.world.snapshot)

    def context(self):
        return {
            "output": self.root,
            "evidence": self.root / "evidence",
            "submission": self.root / "missing.yaml",
            "options": {"mode": "stub"},
        }

    def test_lost_worker_retains_world_without_inventing_native_outcome_or_acceptance(self):
        self.world.prepare()
        context = self.context()
        with patch.object(HOOKS, "_post", side_effect=lambda *_args: self.world.snapshot()):
            trial = HOOKS.snapshot(context)
            self.assertIsNone(trial["native_execution"])
            self.assertFalse(trial["terminal_completion"])
            self.assertIsNone(trial["submission"])
            self.assertNotIn("acceptance", trial)
            environment = json.loads((context["evidence"] / "environment-evidence.json").read_text())
            self.assertEqual(environment["state"], {"effect": "committed"})
            self.assertTrue((context["evidence"] / "transport-evidence.json").is_file())
            before = {p.name: p.read_bytes() for p in context["evidence"].iterdir()}
            context["record"] = {"status": "success", "output": {"final_answer": "late", "incident_summary": "late"}}
            self.assertEqual(HOOKS.snapshot(context), trial)
            self.assertEqual({p.name: p.read_bytes() for p in context["evidence"].iterdir()}, before)
        self.upstream.finalize.assert_called_once()

    def test_real_semantic_deadline_persists_without_worker_and_prevents_late_overwrite(self):
        self.world.limit_seconds = 0.02
        self.world.prepare()
        self.world._timer.join(timeout=2)
        self.assertFalse(self.world._timer.is_alive())
        before = {p.name: p.read_bytes() for p in (self.root / "world").iterdir()}
        terminal = self.world.snapshot()
        self.assertEqual(terminal["window"]["termination_reason"], "timeout")
        context = {
            **self.context(),
            "record": {"status": "success", "output": {"final_answer": "late", "incident_summary": "late"}},
        }
        with patch.object(HOOKS, "_post", return_value=terminal):
            self.assertEqual(HOOKS.snapshot(context)["termination_reason"], "timeout")
        self.assertEqual({p.name: p.read_bytes() for p in (self.root / "world").iterdir()}, before)

    def test_snapshot_and_deadline_race_freezes_one_terminal_observation(self):
        self.world.prepare()
        barrier = threading.Barrier(2)
        thread = threading.Thread(target=lambda: (barrier.wait(), self.world.snapshot()))
        thread.start()
        barrier.wait()
        self.world._deadline()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.upstream.finalize.assert_called_once()
        self.assertEqual(len(list((self.root / "world").glob("*-terminal-window.json"))), 1)
        self.assertEqual(len(list((self.root / "world").glob("*-snapshot.json"))), 1)

    def test_environment_collection_failure_preserves_transport_and_unknown_native_record(self):
        self.upstream.finalize.side_effect = RuntimeError("unavailable")
        self.world.prepare()
        context = self.context()
        with patch.object(HOOKS, "_post", side_effect=lambda *_args: self.world.snapshot()):
            trial = HOOKS.snapshot(context)
        self.assertEqual(trial["evidence_errors"], [{"source": "environment", "error": "RuntimeError"}])
        self.assertIsNone(trial["native_execution"])
        self.assertFalse((context["evidence"] / "environment-evidence.json").exists())
        self.assertTrue((context["evidence"] / "transport-evidence.json").is_file())
        self.assertTrue((context["evidence"] / "native-record.json").is_file())


class CheckoutTerminalTests(unittest.TestCase):
    def test_planner_rejects_ambiguous_yaml_with_the_shared_profile_parser(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "submission.yaml"
            for text in ("workflow: first\nworkflow: last\n", "1: value\n"):
                path.write_text(text)
                with self.assertRaises(Invalid):
                    HOOKS.plan(path, {})

    def test_exact_markdown_and_narrative_contract_is_checkout_owned(self):
        markdown = "# Incident\n\nEUR → fixed  \n"
        reason, submission = HOOKS.terminal_submission(
            {"status": "success", "output": {"final_answer": "Fixed", "incident_summary": markdown}}, 1, "run", 30
        )
        self.assertEqual(reason, "completed")
        self.assertEqual(submission["artifacts"][0]["content"].encode(), markdown.encode())
        self.assertEqual(submission["artifacts"][0]["name"], "incident-summary.md")
        self.assertEqual(
            HOOKS.terminal_submission({"status": "success", "output": {}}, 1, "run", 30), ("protocol_error", None)
        )
        self.assertEqual(HOOKS.terminal_submission({"status": "success"}, 31, "run", 30), ("timeout", None))

    def test_admin_http_redirect_is_rejected_without_forwarding_credentials(self):
        forwarded = []

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "/destination")
                self.end_headers()

            def do_GET(self):
                forwarded.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                credentials = Path(temporary) / "credentials.json"
                credentials.write_text(json.dumps({"admin_token": "private-admin"}))
                context = {
                    "options": {
                        "world_url": f"http://127.0.0.1:{server.server_port}",
                        "world_credentials": str(credentials),
                    }
                }
                with self.assertRaises(HTTPError) as raised:
                    HOOKS._post(context, "/prepare")
                self.assertEqual(raised.exception.code, 302)
                self.assertEqual(forwarded, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_environment_has_no_host_process_or_generic_dispatch_dependency(self):
        code = (DIRECTORY / "server.py").read_text() + (DIRECTORY / "hooks.py").read_text()
        for forbidden in (
            "subprocess",
            "TrialHost",
            "sapi_config_lab.execute",
            "sapi_config_lab.coordinate",
            "sapi_config_lab.evaluate",
        ):
            self.assertNotIn(forbidden, code)


@unittest.skipUnless(AVAILABLE, "Requires verified external AutoWFBench checkout and benchmark extra")
class CheckoutNativeWorldTests(unittest.TestCase):
    def setUp(self):
        SOURCE.verify()
        self.enterContext(patch.dict(sys.modules))
        for name in tuple(sys.modules):
            if name == "autowfbench" or name.startswith("autowfbench."):
                del sys.modules[name]
        self.enterContext(patch.object(sys, "path", [str(SOURCE.root), *sys.path]))
        self.enterContext(patch.object(sys, "dont_write_bytecode", True))
        self.enterContext(patch.dict("os.environ", {"AUTOWFBENCH_ROOT": str(SOURCE.root)}))
        self.upstream = importlib.import_module("autowfbench.runtime.environment")
        contracts = importlib.import_module("autowfbench.core.contracts")
        self.package = contracts.load_challenge(SERVER.CHALLENGE)
        self.world = SERVER.World(self.upstream.ChallengeEnvironment(self.package, 0), self.package["definition"])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), SERVER.handler_for(self.world))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(self.temporary)
        credentials = root / "credentials.json"
        credentials.write_text(
            json.dumps({"tool_token": self.world._candidate_token, "admin_token": self.world._admin_token})
        )
        self.context = {
            "root": DIRECTORY.parent,
            "output": root,
            "evidence": root / "evidence",
            "submission": root / "submission.yaml",
            "plan": None,
            "options": {
                "world_url": f"http://127.0.0.1:{self.server.server_port}",
                "world_credentials": str(credentials),
                "mode": "stub",
            },
        }

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, route, data, token):
        request = Request(
            self.context["options"]["world_url"] + route,
            data=json.dumps(data).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as error:
            error.close()
            raise

    def test_real_oracle_tool_flow_and_frozen_trial_evidence(self):
        self.context["submission"].write_bytes((DIRECTORY.parent / "solution/config.yaml").read_bytes())
        binding = HOOKS.prepare(self.context)
        self.assertEqual(binding.operation_url, self.context["options"]["world_url"] + "/tools")
        self.assertGreater(binding.deadline_at, 0)
        for index, (operation, arguments) in enumerate(CHECKOUT_ORACLE_ACTIONS):
            receipt = self.post(
                "/tools",
                {"operation": operation, "arguments": arguments, "operation_id": str(index)},
                binding.operation_token,
            )
            self.assertTrue(receipt["ok"], receipt)
        self.context["record"] = {
            "status": "success",
            "n8n_version": "2.41.5",
            "output": {"final_answer": "Fixed", "incident_summary": "# Incident\n\nFixed EUR.\n"},
        }
        trial = HOOKS.snapshot(self.context)
        self.assertTrue(trial["native_execution"])
        self.assertTrue(trial["terminal_completion"])
        environment = json.loads((self.context["evidence"] / "environment-evidence.json").read_text())
        self.assertTrue(all(environment["checks"].values()), environment["checks"])
        before = {p.name: p.read_bytes() for p in self.context["evidence"].iterdir()}
        self.assertEqual(HOOKS.snapshot(self.context), trial)
        self.assertEqual({p.name: p.read_bytes() for p in self.context["evidence"].iterdir()}, before)
        self.assertEqual(self.world.call("source.read")["error"]["code"], "RUN_CLOSED")
        serialized = b"".join(before.values())
        self.assertNotIn(self.world._admin_token.encode(), serialized)
        self.assertNotIn(self.world._candidate_token.encode(), serialized)

    def test_nop_records_fresh_world_without_fabricated_native_success(self):
        HOOKS.prepare(self.context)
        self.context["record"] = {"status": "missing_submission", "output": None}
        trial = HOOKS.snapshot(self.context)
        self.assertFalse(trial["terminal_completion"])
        self.assertFalse(trial["native_execution"])
        self.assertIsNone(trial["submission"])
        self.assertIsNone(trial["submission_sha256"])
        environment = json.loads((self.context["evidence"] / "environment-evidence.json").read_text())
        self.assertEqual(environment["tool_calls"], 0)
        self.assertFalse(all(environment["checks"].values()))

    def test_candidate_cannot_bind_or_finalize_world(self):
        for route in ("/prepare", "/snapshot"):
            with self.assertRaises(HTTPError) as raised:
                self.post(route, {}, self.world._candidate_token)
            self.assertEqual(raised.exception.code, 401)
        with self.assertRaises(HTTPError) as raised:
            self.post("/tools", {"operation": "source.read", "arguments": {}}, self.world._admin_token)
        self.assertEqual(raised.exception.code, 401)
        self.assertIsNone(self.world.started)
        HOOKS.prepare(self.context)
        with self.assertRaises(HTTPError) as raised:
            HOOKS.prepare(self.context)
        self.assertEqual(raised.exception.code, 400)

    def test_receipt_replay_conflict_and_ambiguity_never_redispatch(self):
        HOOKS.prepare(self.context)
        first = self.world.call("source.read", operation_id="read")
        self.assertEqual(self.world.call("source.read", operation_id="read"), first)
        self.assertEqual(self.world.world.calls, 1)
        self.assertEqual(
            self.world.call("tests.run", operation_id="read")["error"]["code"], "PROJECT_OPERATION_ID_CONFLICT"
        )
        with patch.object(self.world.world, "execute", side_effect=OSError("Unknown outcome")) as execute:
            failure = self.world.call("source.read", operation_id="ambiguous", max_attempts=3)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(failure["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
        self.assertEqual(self.world.call("tests.run")["error"]["code"], "PROJECT_AMBIGUOUS_OUTCOME")
        self.assertTrue(self.world.transport_evidence()["ambiguous_outcome"])

    def test_fresh_world_and_deadline_stop_mutation(self):
        HOOKS.prepare(self.context)
        self.world.call(
            "checkout.patch", {"old": "charge_card(currency, amount)", "new": "charge_card(amount, currency)"}
        )
        fresh = SERVER.World(self.upstream.ChallengeEnvironment(self.package, 0), self.package["definition"])
        fresh.prepare()
        self.assertEqual(fresh.world.calls, 0)
        self.assertNotEqual(fresh._candidate_token, self.world._candidate_token)
        self.assertIn("charge_card(currency, amount)", fresh.call("source.read")["value"]["content"])
        self.world.started -= self.world.limit_seconds + 1
        self.assertEqual(self.world.call("source.read")["error"]["code"], "RUN_CLOSED")
        self.assertEqual(self.world.world.calls, 1)
