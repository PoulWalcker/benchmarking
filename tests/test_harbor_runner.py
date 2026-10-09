"""Native Harbor dispatch preserves phase limits and durable partial jobs."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.harbor_integration.runner import run_job
from sapi_config_lab.harbor_integration.task_config import validate_config

TASK = """schema_version = "1.4"
artifacts = [
  {source="/logs/artifacts", destination="discarded-convention", exclude=["*"]},
  {source="/submission/config.yaml", destination="submission/config.yaml"}
]
[agent]
timeout_sec = 17
user = "1000"
[environment]
build_timeout_sec = 23
cpus = 2
memory_mb = 1024
[verifier]
timeout_sec = 31
environment_mode = "separate"
[[verifier.collect]]
command = "python3 /opt/sapi-submission.py collect"
user = "root"
timeout_sec = 10
[verifier.environment]
build_timeout_sec = 37
cpus = 3
memory_mb = 2048
"""


class NativeHarborRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tasks = self.root / "tasks"
        self.jobs = self.root / "durable/jobs"
        self.log = self.root / "harbor.log"

    def task(self, name: str = "example", text: str = TASK) -> Path:
        task = self.tasks / name
        task.mkdir(parents=True)
        (task / "task.toml").write_text(text)
        return task

    def run_job(self, tasks: Path | None = None, **arguments) -> int:
        return run_job(["harbor"], tasks or self.tasks, self.jobs, "control", "oracle", self.log, **arguments)

    def test_dispatch_preserves_native_phase_limits_and_resources_without_outer_timeout(self):
        task = self.task()
        original = (task / "task.toml").read_bytes()
        with patch("sapi_config_lab.harbor_integration.runner.subprocess.run") as dispatch:
            dispatch.return_value.returncode = 7
            self.assertEqual(
                self.run_job(task, agent_key="model=test", attempts="2", verifier_env=["SAPI_MODE=control"]), 7
            )
        command = dispatch.call_args.args[0]
        self.assertEqual(
            command,
            [
                "harbor",
                "run",
                "--path",
                str(task),
                "--agent",
                "oracle",
                "--ak",
                "model=test",
                "--n-attempts",
                "2",
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--jobs-dir",
                str(self.jobs),
                "--job-name",
                "control",
                "--verifier-env",
                "SAPI_MODE=control",
                "--force-build",
            ],
        )
        self.assertNotIn("timeout", dispatch.call_args.kwargs)
        self.assertEqual(dispatch.call_args.kwargs["stderr"], subprocess.STDOUT)
        self.assertEqual(dispatch.call_args.kwargs["stdout"].name, str(self.log))
        self.assertEqual((task / "task.toml").read_bytes(), original)
        config = validate_config(original.decode())
        self.assertEqual(config.agent.timeout_sec, 17)
        self.assertEqual(config.verifier.timeout_sec, 31)
        self.assertEqual(
            (config.environment.build_timeout_sec, config.environment.cpus, config.environment.memory_mb),
            (23, 2, 1024),
        )
        self.assertEqual(
            (
                config.verifier.environment.build_timeout_sec,
                config.verifier.environment.cpus,
                config.verifier.environment.memory_mb,
            ),
            (37, 3, 2048),
        )

    def test_unsupported_capabilities_fail_before_dispatch_in_either_environment(self):
        requests = (
            "gpus = 1",
            'gpu_types = ["A100"]',
            "storage_mb = 4096",
            'tpu = {type="v4", topology="2x2"}',
        )
        for section in ("environment", "verifier.environment"):
            for request in requests:
                with self.subTest(section=section, request=request):
                    text = TASK.replace(f"[{section}]\n", f"[{section}]\n{request}\n")
                    with self.assertRaises(ValueError):
                        validate_config(text)

    def test_all_selected_tasks_are_validated_before_dispatch(self):
        self.task("a-valid")
        self.task("z-invalid", TASK.replace("[environment]\n", "[environment]\ngpus = 1\n"))
        with patch("sapi_config_lab.harbor_integration.runner.subprocess.run") as dispatch:
            with self.assertRaises(ValueError):
                self.run_job()
        dispatch.assert_not_called()
        self.assertFalse(self.jobs.exists())

    def test_existing_job_is_refused_without_overwriting_evidence_or_log(self):
        self.task()
        job = self.jobs / "control"
        job.mkdir(parents=True)
        evidence = job / "partial.json"
        evidence.write_text('{"execution": null}')
        self.log.write_text("previous dispatch\n")
        with patch("sapi_config_lab.harbor_integration.runner.subprocess.run") as dispatch:
            with self.assertRaises(ValueError):
                self.run_job()
        dispatch.assert_not_called()
        self.assertEqual(evidence.read_text(), '{"execution": null}')
        self.assertEqual(self.log.read_text(), "previous dispatch\n")

    def test_interruption_preserves_partial_native_job_and_refuses_redispatch(self):
        self.task()
        evidence = self.jobs / "control/example__partial/verifier/world/001.json"

        def interrupted(command, **options):
            self.assertEqual(Path(command[command.index("--jobs-dir") + 1]), self.jobs)
            self.assertNotIn("timeout", options)
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"state": "reserved"}')
            options["stdout"].write("Harbor started\n")
            raise KeyboardInterrupt

        with patch("sapi_config_lab.harbor_integration.runner.subprocess.run", side_effect=interrupted) as dispatch:
            with self.assertRaises(KeyboardInterrupt):
                self.run_job()
            with self.assertRaises(ValueError):
                self.run_job()
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(evidence.read_text(), '{"state": "reserved"}')
        self.assertEqual(self.log.read_text(), "Harbor started\n")
