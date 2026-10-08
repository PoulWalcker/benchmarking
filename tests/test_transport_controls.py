"""Redirect rejection at real clients and narrowly scoped hosted nop exceptions."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sapi_config_lab.coordinate import controls, transport
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.evaluation import control_passed as selected_control_passed
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.execute.host import checked_harbor, running_containers
from sapi_config_lab.paths import workspace_root


def control_passed(agent, trial):
    return selected_control_passed(
        agent, trial, select_benchmarks(workspace_root() / "benchmarks", (trial["task_name"],))[0]
    )


class NativeTransportTaskTests(unittest.TestCase):
    def test_independent_fixture_compiles_without_the_global_catalog(self):
        from sapi_config_lab.contracts import CompileOptions
        from sapi_config_lab.coordinate.backend import N8nBackend
        from sapi_config_lab.profile import read_bindings

        fixture = controls.ROOT / "tests/support/native-transport/tests"
        with patch.object(transport.profile, "read_bindings", wraps=read_bindings):
            compiled = N8nBackend((fixture / "operations.js").read_text()).compile(
                transport.probe_config(),
                read_bindings(fixture / "bindings.yaml"),
                CompileOptions("live", "http://localhost:123"),
            )
        self.assertIn("transport.echo", json.dumps(compiled.document))
        self.assertNotIn("ticket", json.dumps(transport.probe_config()))

    def test_harbor_owns_dispatch_and_skip_build_reuses_the_pinned_image(self):
        for skip_build in (False, True):
            with self.subTest(skip_build=skip_build), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                run = Run(output, {}, source_manifest(), "transport-test", harbor_argv=["harbor"])

                def dispatch(argv, *, output=output, skip_build=skip_build, **kwargs):
                    self.assertEqual(argv[:2], ["harbor", "run"])
                    self.assertNotIn("timeout", kwargs)
                    self.assertIn("--no-delete", argv)
                    self.assertEqual("--force-build" in argv, not skip_build)
                    trial = output / "jobs/transport/native"
                    evidence = trial / "verifier/transport"
                    evidence.mkdir(parents=True)
                    (evidence / "summary.json").write_text(json.dumps({"passed": True, "cases": []}))
                    (trial / "result.json").write_text(
                        json.dumps({"exception_info": None, "verifier_result": {"rewards": {"reward": 1.0}}})
                    )
                    return SimpleNamespace(returncode=0)

                with (
                    patch.object(controls.subprocess, "run", side_effect=dispatch),
                    patch("sapi_config_lab.coordinate.runs.image_id", return_value="sha256:control-image"),
                    patch("sapi_config_lab.coordinate.runs.pin_base_image", return_value="frozen-control-image"),
                ):
                    result = controls.transport_probe(run, skip_build=skip_build)
                self.assertTrue(result["passed"])
                task = output / "transport-task"
                self.assertEqual("docker_image = " in (task / "task.toml").read_text(), skip_build)
                if skip_build:
                    self.assertIn('docker_image = "frozen-control-image"', (task / "task.toml").read_text())
                    self.assertIn("image: frozen-control-image", (task / "environment/docker-compose.yaml").read_text())
                    self.assertEqual(run.report["image_id"], "sha256:control-image")
                self.assertEqual(result["evidence_path"], "jobs/transport/native/verifier/transport")

    def test_missing_native_terminal_result_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Run(Path(directory), {}, source_manifest(), "transport-test", harbor_argv=["harbor"])
            with patch.object(controls.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
                self.assertFalse(controls.transport_probe(run)["passed"])


@unittest.skipUnless(os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "native Docker proof is opt-in")
class NativeTransportDockerTests(unittest.TestCase):
    def test_success_and_verifier_failure_both_leave_no_containers(self):
        (controls.ROOT / "reports/migration-08").mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="native-transport-", dir=controls.ROOT / "reports/migration-08"))
        harbor, _ = checked_harbor()
        before = running_containers()
        original_copy = shutil.copytree
        reports = {}
        for mode in ("success", "failure"):
            output = root / mode
            output.mkdir()
            run = Run(output, {}, source_manifest(), "transport-08", harbor_argv=harbor)

            def stage(source, destination, *args, mode=mode, **kwargs):
                result = original_copy(source, destination, *args, **kwargs)
                if mode == "failure" and Path(source).name == "native-transport":
                    script = Path(destination) / "tests/test.sh"
                    script.write_text(script.read_text().replace("printf '1", "exit 37\nprintf '1"))
                return result

            with patch.object(controls.shutil, "copytree", side_effect=stage):
                probe = controls.transport_probe(run, skip_build=mode == "failure")
            self.assertEqual(probe["passed"], mode == "success")
            self.assertEqual(len(probe["cases"]), 19)
            self.assertTrue(all(row["passed"] for row in probe["cases"]))
            self.assertEqual(running_containers(), before)
            reports[mode] = probe
        (root / "inspection.json").write_text(
            json.dumps({"before": before, "after": running_containers(), "probes": reports}, indent=2)
        )


class RedirectTests(unittest.TestCase):
    def test_author_does_not_follow_a_redirect(self):
        self.redirect_client("author")

    def redirect_client(self, client):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()

            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "connection.json").write_text(
                    json.dumps(
                        {
                            "url": url,
                            "token": "secret",
                            "evaluation_seconds": 30,
                        }
                    )
                )
                with self.assertRaises(HTTPError) as error:
                    from sapi_config_lab.harbor_integration.yaml_agent import WrapperYamlAgent

                    agent = object.__new__(WrapperYamlAgent)
                    agent.upstream = url + "/author"
                    agent.request("public task")
                self.assertEqual(error.exception.code, 302)
                self.assertEqual(len(seen), 1)
                self.assertNotEqual(seen[0][0], "/target")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class HostedNopPolicyTests(unittest.TestCase):
    def trial(self, *, quality=None, exception=None, rewards=None):
        return {
            "task_name": "checkout-recovery",
            "exception": exception,
            "rewards": rewards,
            "result": {"execution": False, "acceptance": False, "quality": quality},
        }

    def test_unscored_nop_accepts_only_the_expected_missing_reward_exception(self):
        quality = {"status": "judge_failed", "score_0_10": None, "normalized_reward": None}
        for exception, expected in (
            (None, True),
            ("RewardFileNotFoundError", True),
            ("TimeoutError", False),
            ("RuntimeError", False),
        ):
            with self.subTest(exception=exception):
                trial = self.trial(quality=quality, exception={"exception_type": exception} if exception else None)
                self.assertEqual(control_passed("nop", trial), expected)
        trial = self.trial(quality=quality, rewards={"reward": 1.0})
        self.assertFalse(control_passed("nop", trial))
        quality["normalized_reward"] = 0.0
        self.assertFalse(control_passed("nop", self.trial(quality=quality)))

    def test_deterministic_nop_requires_zero_reward_and_no_exception(self):
        self.assertTrue(control_passed("nop", self.trial(rewards={"reward": 0.0})))
        self.assertFalse(control_passed("nop", self.trial()))
        self.assertFalse(
            control_passed(
                "nop",
                self.trial(
                    rewards={"reward": 0.0},
                    exception={"exception_type": "RewardFileNotFoundError"},
                ),
            )
        )
