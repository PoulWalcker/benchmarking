"""Native experiment guards fail before dispatch without descriptor staging."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.evaluation import control_passed
from sapi_config_lab.coordinate.native_tasks import image_tags, policy, select_tasks
from sapi_config_lab.coordinate.provenance import source_manifest
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
            target = "public" if area == "environment" else "verifier"
            (self.task / area / "Dockerfile").write_text(f"FROM sapi-native-invoice-total-{target}:phase1\n")
        (self.task / "task.toml").write_bytes((workspace_root() / "tasks/invoice-total/task.toml").read_bytes())
        (self.task / "instruction.md").write_text("unchanged prompt\n")
        for name in ("images.Dockerfile", "images.Dockerfile.dockerignore", "task.md", "bindings.yaml"):
            (self.task / name).write_bytes((workspace_root() / "tasks/invoice-total" / name).read_bytes())
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

    def test_native_build_selects_only_required_tasks_and_records_their_images(self):
        second = self.root / "tasks/second"
        omitted = self.root / "tasks/omitted"
        for task in (second, omitted):
            shutil.copytree(self.task, task)
            for area, target in (("environment", "public"), ("tests", "verifier")):
                tag = f"sapi-native-{task.name}-{target}:phase1"
                (task / area / "Dockerfile").write_text(f"FROM {tag}\n")
                self.images[tag] = "sha256:" + tag
        for tasks, expected_tags in (
            ((self.task,), ("sapi-native-invoice-total-public:phase1", "sapi-native-invoice-total-verifier:phase1")),
            (
                (second, self.task),
                (
                    "sapi-native-invoice-total-public:phase1",
                    "sapi-native-invoice-total-verifier:phase1",
                    "sapi-native-second-public:phase1",
                    "sapi-native-second-verifier:phase1",
                ),
            ),
        ):
            expected_images = {tag: "sha256:" + tag for tag in expected_tags}
            with (
                self.subTest(tasks=tasks),
                patch("sapi_config_lab.coordinate.runs.run_logged", return_value=0) as build,
            ):
                self.run.use_native_tasks(tasks, build=True)
                self.assertEqual(
                    build.call_args.args,
                    (
                        ["sh", str(self.root / "infra/native/build.sh"), *[task.name for task in tasks]],
                        self.output / "native-build.log",
                    ),
                )
                self.assertEqual(
                    json.loads(self.cache.read_text()), {"sources": self.sources, "images": expected_images}
                )
                self.assertEqual(self.run.report["native_images"], expected_images)

    def test_native_build_keeps_called_process_error_and_stage(self):
        with patch("sapi_config_lab.coordinate.runs.run_logged", return_value=7) as dispatch:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                self.run.use_native_tasks((self.task,), build=True)
        argv = ["sh", str(self.root / "infra/native/build.sh"), "invoice-total"]
        self.assertEqual(raised.exception.cmd, argv)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(dispatch.call_args.args, (argv, self.output / "native-build.log"))
        self.assertEqual(dispatch.call_args.kwargs, {"stage": "native build", "timeout": None})

    def test_reuse_names_a_selected_image_absent_from_the_verified_build(self):
        missing = "sapi-native-invoice-total-public:phase1"
        recorded = {tag: identity for tag, identity in self.images.items() if tag != missing}
        self.cache.write_text(json.dumps({"sources": self.sources, "images": recorded}))
        with patch("sapi_config_lab.coordinate.runs.run_job") as dispatch:
            with self.assertRaisesRegex(
                ValueError,
                f"Native image {missing} is not in the verified build; rebuild without --skip-build",
            ):
                self.run.use_native_tasks((self.task,))
            dispatch.assert_not_called()

    def test_reuse_refuses_a_missing_local_image_or_differing_image_id(self):
        tag = "sapi-native-invoice-total-public:phase1"
        for missing in (True, False):
            error = RuntimeError(f"Docker image {tag} is missing; rebuild without --skip-build")
            with (
                self.subTest(missing=missing),
                patch(
                    "sapi_config_lab.coordinate.runs.image_id",
                    side_effect=error if missing else None,
                    return_value="sha256:changed",
                ),
                patch("sapi_config_lab.coordinate.runs.run_job") as dispatch,
            ):
                with self.assertRaisesRegex(
                    RuntimeError if missing else ValueError,
                    str(error) if missing else "Native image differs from its verified build",
                ):
                    self.run.use_native_tasks((self.task,))
                dispatch.assert_not_called()

    def test_a_recipe_changed_during_build_cannot_replace_the_verified_record(self):
        self.run.sources = source_manifest(self.root)
        original = self.cache.read_bytes()

        def build(*args, **kwargs):
            recipe = self.task / "images.Dockerfile"
            recipe.write_text(recipe.read_text() + "# Edited during the build\n")
            return 0

        with (
            patch("sapi_config_lab.coordinate.runs.source_manifest", side_effect=lambda: source_manifest(self.root)),
            patch("sapi_config_lab.coordinate.runs.run_logged", side_effect=build),
            patch("sapi_config_lab.coordinate.runs.run_job") as dispatch,
        ):
            with self.assertRaisesRegex(RuntimeError, r"Sources changed \(after-native-build\)"):
                self.run.use_native_tasks((self.task,), build=True)
            dispatch.assert_not_called()
        self.assertEqual(self.cache.read_bytes(), original)

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
