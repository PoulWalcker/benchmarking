"""The outgoing WBS seam uses real returned YAML and reserves before dispatch."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.runtime.rebuilder import WrapperRebuilder
from sapi_config_lab.core import profile


class RebuilderTests(unittest.TestCase):
    def test_actual_response_is_preserved_and_same_reservation_cannot_repeat(self):
        source = profile.read(workspace_root() / "configs/05-digest-lifecycle.yaml")
        target = {"id": "daily-digest", "revision": 2}
        candidate = copy.deepcopy(source)
        candidate["workflow"]["revision"] = 2
        candidate["activation"]["workflow_ref"] = target
        answer = yaml.safe_dump(candidate, sort_keys=False)
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def read(self, limit):
                    return json.dumps(
                        {"ok": True, "exit_code": 0, "output": answer, "stderr": "model: gpt-6-astra\n"}
                    ).encode()

            def dispatch(request, timeout):
                reserved = json.loads((artifacts / "dispatch.json").read_text())
                self.assertEqual(reserved["status"], "reserved")
                self.assertEqual(reserved["wrapper_attempts"], 1)
                self.assertEqual(timeout, 185)
                self.assertIn("wrong IDs", json.loads(request.data)["prompt"])
                return Response()

            builder = WrapperRebuilder("http://127.0.0.1:8765/run")
            with patch("sapi_config_lab.runtime.rebuilder.urlopen", side_effect=dispatch) as outgoing:
                result = builder(source, {"passed": False, "findings": ["wrong IDs"]}, target, artifacts)
                self.assertEqual(result, candidate)
                self.assertEqual((artifacts / "candidate.yaml").read_text(), answer)
                original_prompt = (artifacts / "prompt.txt").read_bytes()
                with self.assertRaises(FileExistsError):
                    builder(source, {}, target, artifacts)
                self.assertEqual((artifacts / "prompt.txt").read_bytes(), original_prompt)
                self.assertEqual(outgoing.call_count, 1)
            audit = json.loads((artifacts / "dispatch.json").read_text())
            self.assertEqual(audit["status"], "returned")
            self.assertEqual(audit["wrapper_completions"], 1)
            self.assertEqual(audit["model"], "gpt-6-astra")
            self.assertIsNone(audit["provider_call_count"])

    def test_unknown_wrapper_outcome_is_saved_and_not_retried(self):
        source = profile.read(workspace_root() / "configs/05-digest-lifecycle.yaml")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            builder = WrapperRebuilder("http://127.0.0.1:8765/run")
            with patch("sapi_config_lab.runtime.rebuilder.urlopen", side_effect=TimeoutError) as outgoing:
                with self.assertRaises(TimeoutError):
                    builder(source, {}, {"id": "daily-digest", "revision": 2}, path)
                with self.assertRaises(FileExistsError):
                    builder(source, {}, {"id": "daily-digest", "revision": 2}, path)
                self.assertEqual(outgoing.call_count, 1)
            self.assertEqual(json.loads((path / "dispatch.json").read_text())["status"], "failed_or_unknown")

    def test_missing_or_wrong_model_and_boolean_exit_code_fail_closed(self):
        source = profile.read(workspace_root() / "configs/05-digest-lifecycle.yaml")
        for exit_code, stderr in [(False, "model: gpt-6-astra\n"), (0, ""), (0, "model: other-model\n")]:
            with self.subTest(exit_code=exit_code, stderr=stderr), tempfile.TemporaryDirectory() as directory:
                response = {"ok": True, "exit_code": exit_code, "stderr": stderr, "output": yaml.safe_dump(source)}
                with patch(
                    "sapi_config_lab.runtime.rebuilder.urlopen", return_value=io.BytesIO(json.dumps(response).encode())
                ) as outgoing:
                    with self.assertRaises(profile.Invalid):
                        WrapperRebuilder("http://127.0.0.1:8765/run")(
                            source, {}, {"id": "daily-digest", "revision": 2}, Path(directory)
                        )
                self.assertEqual(outgoing.call_count, 1)
                self.assertFalse((Path(directory) / "candidate.yaml").exists())
