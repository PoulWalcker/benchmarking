"""Redirect rejection at real clients and narrowly scoped hosted nop exceptions."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from sapi_config_lab.coordinate import hosted_worker
from sapi_config_lab.coordinate.evaluation import control_passed


class RedirectTests(unittest.TestCase):
    def test_hosted_worker_does_not_forward_its_bearer_to_a_redirect(self):
        self.redirect_client("worker")

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
                    if client == "worker":
                        with patch.object(hosted_worker, "TESTS", root):
                            hosted_worker.run()
                    else:
                        from sapi_config_lab.author.agent import WrapperYamlAgent

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
