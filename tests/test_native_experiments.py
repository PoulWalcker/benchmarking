"""Native experiment guards fail before dispatch without descriptor staging."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.evaluation import control_passed
from sapi_config_lab.coordinate.native_tasks import image_tags, policy, select_tasks
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.paths import workspace_root


class NativeExperimentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = self.root / "tasks/invoice-total"
        for area in ("environment", "tests"):
            (self.task / area).mkdir(parents=True)
            (self.task / area / "Dockerfile").write_text(f"FROM sapi-native-{area}:test\n")
        (self.task / "task.toml").write_bytes((workspace_root() / "tasks/invoice-total/task.toml").read_bytes())
        (self.task / "instruction.md").write_text("unchanged prompt\n")
        (self.root / "reports").mkdir()
        self.sources = {"task": "frozen"}
        self.images = {tag: "sha256:" + tag for tag in image_tags((self.task,))}
        self.cache = self.root / "reports/native-image-build.json"
        self.cache.write_text(json.dumps({"sources": self.sources, "images": self.images}))
        self.output = self.root / "run"
        self.output.mkdir()
        self.run = Run(self.output, {}, self.sources, "native", harbor_argv=["harbor"])
        for name, value in (("workspace_root", self.root), ("source_manifest", self.sources)):
            patched = patch("sapi_config_lab.coordinate.runs." + name, return_value=value)
            patched.start()
            self.addCleanup(patched.stop)
        patched = patch("sapi_config_lab.coordinate.runs.image_id", side_effect=lambda tag: self.images[tag])
        patched.start()
        self.addCleanup(patched.stop)

    def test_native_selection_does_not_require_a_descriptor_or_import_task_code(self):
        self.assertEqual(select_tasks(self.root / "tasks"), (self.task,))
        self.assertEqual(policy(self.task)["catalogs"], ["full", "scenario"])
        self.assertFalse((self.task / "scenario.json").exists())
        for names in ([], ["unknown"], ["invoice-total", "invoice-total"]):
            with self.assertRaises(ValueError):
                select_tasks(self.root / "tasks", names)
        (self.task / "task.toml").write_text('[metadata.sapi]\nentrypoint = "candidate.py"\n')
        with self.assertRaisesRegex(ValueError, "policy"):
            policy(self.task)

    def test_dispatch_uses_the_unchanged_native_directory_and_durable_job_tree(self):
        self.run.use_native_tasks((self.task,))
        with patch("sapi_config_lab.coordinate.runs.run_job", return_value=0) as dispatch:
            self.run.harbor("oracle-invoice", self.task, "oracle")
            self.assertEqual(dispatch.call_args.args[1], self.task)
            self.assertEqual(dispatch.call_args.args[2], self.output / "jobs")
            with self.assertRaisesRegex(ValueError, "twice"):
                self.run.harbor("oracle-invoice", self.task, "oracle")
        self.assertFalse(hasattr(self.run, "staging"))
        self.assertFalse((self.output / "task-packages").exists())
        self.assertEqual(self.run.report["native_images"], self.images)

    def test_changed_source_image_task_prompt_or_input_blocks_native_dispatch(self):
        self.run.use_native_tasks((self.task,))
        mutations = {
            "source": lambda: setattr(self.run, "sources", {"different": "source"}),
            "image": lambda: self.images.update({next(iter(self.images)): "sha256:changed"}),
            "task prompt": lambda: (self.task / "instruction.md").write_text("changed"),
            "input snapshot": lambda: (self.output / "native-inputs.json").write_text("{}"),
        }
        for name, mutate in mutations.items():
            original_images = dict(self.images)
            prompt = (self.task / "instruction.md").read_bytes()
            inputs = (self.output / "native-inputs.json").read_bytes()
            with self.subTest(name=name), patch("sapi_config_lab.coordinate.runs.run_job") as dispatch:
                mutate()
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    self.run.harbor("changed", self.task, "oracle")
                dispatch.assert_not_called()
            self.run.sources = self.sources
            self.images.clear()
            self.images.update(original_images)
            (self.task / "instruction.md").write_bytes(prompt)
            (self.output / "native-inputs.json").write_bytes(inputs)

    def test_a_stale_build_cannot_authorize_native_experiments(self):
        for record in ({"sources": {}, "images": self.images}, {"sources": self.sources, "images": {}}):
            self.cache.write_text(json.dumps(record))
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.run.use_native_tasks((self.task,))

    def test_native_build_keeps_called_process_error_and_stage(self):
        with patch("sapi_config_lab.coordinate.runs.run_logged", return_value=7) as dispatch:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                self.run.use_native_tasks((self.task,), build=True)
        argv = ["sh", str(self.root / "infra/native/build.sh")]
        self.assertEqual(raised.exception.cmd, argv)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(dispatch.call_args.args, (argv, self.output / "native-build.log"))
        self.assertEqual(dispatch.call_args.kwargs, {"stage": "native build", "timeout": None})

    def test_missing_quality_is_not_a_measured_zero_for_world_nop(self):
        trial = {
            "acceptance": {"passed": False},
            "result": {"execution": None, "acceptance": False, "quality": None},
            "rewards": None,
            "exception": {"exception_type": "RewardFileNotFoundError"},
        }
        self.assertTrue(control_passed("nop", trial, reference_reward=0.732))
        self.assertFalse(control_passed("nop", trial))
        trial["exception"] = {"exception_type": "DockerError"}
        self.assertFalse(control_passed("nop", trial, reference_reward=0.732))
