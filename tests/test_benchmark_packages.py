"""Positive task contexts and opt-in pinned Harbor/Docker privacy controls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import uuid

import yaml

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.harbor_integration.submission import collect, read_submission
from sapi_config_lab.harbor_integration.tasks import stage_benchmark, validate_config
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
PRIVATE = b"PRIVATE_FIXTURE_03_721d"
REFERENCE = b"REFERENCE_03_b023"
CREDENTIAL = b"CREDENTIAL_03_118e"
CONFIG = """schema_version = "1.4"
artifacts = [
  {source="/logs/artifacts", destination="discarded-convention", exclude=["*"]},
  {source="/submission/config.yaml", destination="submission/config.yaml"}
]
[agent]
user = "1000"
timeout_sec = 20
[environment]
cpus = 1
memory_mb = 512
build_timeout_sec = 600
[verifier]
timeout_sec = 30
environment_mode = "separate"
[[verifier.collect]]
command = "python3 /opt/sapi-submission.py collect"
user = "root"
timeout_sec = 10
[verifier.environment]
cpus = 1
memory_mb = 512
"""


def fixture(root):
    directory = root / "synthetic"
    directory.mkdir(parents=True)
    files = {
        "instruction.md": b"Write YAML to /app/submission/config.yaml.\n",
        "bindings.yaml": b"operations: [new_unregistered_operation]\n",
        "operations.js": b"// trusted operation bundle\n",
        "reference.yaml": b"value: " + REFERENCE + b"\n",
        "task.toml": CONFIG.encode(),
        "evaluation/fixtures.json": PRIVATE,
        "evaluation/evaluator.py": b'from ..dependencies.family.rules import EXPECTED\ndef plan(submission, options): return {}\ndef evaluate(evidence, options): return {"expected": EXPECTED}\n',
        "verifier.sh": b"""#!/bin/bash
set -eu
python3 - <<'SCRIPT'
import json, pathlib
from evaluation.evaluator import evaluate
root = pathlib.Path('/logs/verifier')
submission = pathlib.Path('/submission/config.yaml')
(root / 'evidence').mkdir(exist_ok=True)
(root / 'evidence' / 'placement.json').write_text(json.dumps({'submission': submission.exists(), 'convention': list(map(str, pathlib.Path('/logs/artifacts').iterdir())), 'private': pathlib.Path('/tests/payload/evaluation/fixtures.json').read_text(), 'evaluator': evaluate(None, {})}))
(root / 'reward.txt').write_text('1' if submission.exists() else '0')
SCRIPT
""",
    }
    # Relative evaluator imports are resolved under the payload namespace in the image.
    files["verifier.sh"] = files["verifier.sh"].replace(
        b"from evaluation.evaluator", b'import sys; sys.path.insert(0, "/tests")\nfrom payload.evaluation.evaluator'
    )
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (directory / ".env").write_bytes(CREDENTIAL)
    shared = root / "_shared/family"
    shared.mkdir(parents=True)
    (shared / "rules.py").write_text("EXPECTED = 7\n")
    public = ["instruction.md", "bindings.yaml"]
    meta = {
        "version": "sapi-lab-benchmark/v1",
        "id": "synthetic",
        "default": False,
        "public": public,
        "trusted": [name for name in files if name not in [*public, "reference.yaml"]],
        "reference": "reference.yaml",
        "bindings": "bindings.yaml",
        "operations": "operations.js",
        "harbor_task": "task.toml",
        "entrypoints": {role: {"path": "evaluation/evaluator.py", "symbol": role} for role in ("plan", "evaluate")},
        "dependencies": {"family": {"path": "_shared/family", "public": [], "trusted": ["rules.py"]}},
        "controls": {"oracle_acceptance": True, "reference_reward": None},
        "budgets": {"authoring_attempts": 0, "runtime_model_calls": 0, "judge_calls": 0},
        "config": {},
    }
    (directory / "scenario.json").write_text(json.dumps(meta))
    return load_benchmark(root, directory)


def inspect_tar(archive):
    """Read every file, including compressed ancestor layers and large runtime files."""
    count = 0
    for member in archive:
        if member.name.removeprefix("./").startswith(("app/lab/", "tests/", "solution/")):
            raise AssertionError(f"Legacy/private tree in public image: {member.name}")
        if member.isfile():
            count += 1
            stream = archive.extractfile(member)
            tail = b""
            while chunk := stream.read(1024 * 1024):
                content = tail + chunk
                for secret in (PRIVATE, REFERENCE, CREDENTIAL):
                    if secret in content:
                        raise AssertionError(f"Private sentinel in {member.name}")
                tail = content[-128:]
    return count


class BenchmarkPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.benchmark = fixture(self.root / "benchmarks")
        self.task = self.root / "tasks/synthetic"

    def test_positive_contexts_exact_bytes_and_snapshot(self):
        identity = stage_benchmark(self.benchmark, self.task, {"seed": 1})
        public = b"".join(path.read_bytes() for path in (self.task / "environment").rglob("*") if path.is_file())
        for secret in (PRIVATE, REFERENCE, CREDENTIAL):
            self.assertNotIn(secret, public)
        self.assertEqual(
            (self.task / "instruction.md").read_bytes(), (self.benchmark.directory / "instruction.md").read_bytes()
        )
        self.assertEqual(
            (self.task / "environment/payload/bindings.yaml").read_bytes(),
            (self.benchmark.directory / "bindings.yaml").read_bytes(),
        )
        self.assertIn(REFERENCE, (self.task / "solution/config.yaml").read_bytes())
        trusted = b"".join(path.read_bytes() for path in (self.task / "tests").rglob("*") if path.is_file())
        self.assertIn(PRIVATE, trusted)
        self.assertNotIn(REFERENCE, trusted)
        self.assertNotIn(CREDENTIAL, trusted)
        snapshot = json.loads((self.task / "inputs.json").read_text())
        self.assertEqual(snapshot["benchmark"]["sha256"], identity.sha256)
        for name, digest in snapshot["files"].items():
            self.assertEqual(hashlib.sha256((self.task / name).read_bytes()).hexdigest(), digest)
        with self.assertRaisesRegex(ValueError, "already exists"):
            stage_benchmark(self.benchmark, self.task, {})

    def test_root_owned_collection_helper_is_not_writable_with_permissive_host_umask(self):
        previous = os.umask(0)
        try:
            stage_benchmark(self.benchmark, self.task, {})
        finally:
            os.umask(previous)
        self.assertEqual((self.task / "environment/submission.py").stat().st_mode & 0o777, 0o644)

    def test_isolated_declared_imports_and_missing_dependency(self):
        stage_benchmark(self.benchmark, self.task, {})
        script = self.task / "tests/check_imports.py"
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                f"import sys; sys.path.insert(0, {str(script.parent)!r}); exec({script.read_text()!r})",
            ],
            cwd=self.root,
            check=True,
        )
        (self.task / "tests/payload/dependencies/family/rules.py").unlink()
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                f"import sys; sys.path.insert(0, {str(script.parent)!r}); exec({script.read_text()!r})",
            ],
            cwd=self.root,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"ModuleNotFoundError", result.stderr)

    def test_native_configuration_rejects_unsafe_profiles(self):
        validate_config(CONFIG)
        for old, new in [
            ("/submission/config.yaml", "/submission/../tests/test.sh"),
            ('user = "1000"', 'user = "root"'),
            ('exclude=["*"]', "exclude=[]"),
            ('destination="discarded-convention"', 'destination="logs/artifacts"'),
            ("[verifier.environment]", '[verifier.environment]\ndocker_image="legacy:latest"'),
            ('environment_mode = "separate"', 'environment_mode = "shared"'),
        ]:
            with self.subTest(new=new), self.assertRaises(ValueError):
                validate_config(CONFIG.replace(old, new))

    def test_submission_rejects_symlinks_executables_directories_and_extra_files(self):
        source = self.root / "submission"
        source.mkdir()
        path = source / "config.yaml"
        path.write_text("value: 1\n")
        destination = self.root / "protected"
        destination.mkdir()
        collect(source, destination)
        self.assertEqual((destination / "config.yaml").read_text(), "value: 1\n")
        for mode in ("symlink", "directory", "executable", "extra", "tag", "recursive"):
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()
            path.write_text("value: 1\n")
            if mode == "symlink":
                path.unlink()
                path.symlink_to(destination / "config.yaml")
            elif mode == "directory":
                path.unlink()
                path.mkdir()
            elif mode == "executable":
                path.chmod(0o755)
            elif mode == "extra":
                (source / "evil.sh").write_text("echo evil")
            elif mode == "tag":
                (source / "evil.sh").unlink()
                path.write_text("!!python/object:builtins.object {}")
            else:
                path.write_text("&x {self: *x}")
            with self.subTest(mode=mode), self.assertRaises((OSError, ValueError, yaml.YAMLError)):
                read_submission(source)
        link = self.root / "linked"
        link.symlink_to(source, target_is_directory=True)
        with self.assertRaises(OSError):
            read_submission(link)


@unittest.skipUnless(
    os.environ.get("SAPI_RUN_DOCKER_TESTS") == "1", "Set SAPI_RUN_DOCKER_TESTS=1 for real Harbor/Docker controls"
)
class DockerBenchmarkPackagesTests(unittest.TestCase):
    def test_real_separate_verifier_transfer_and_all_public_image_layers(self):
        run = ROOT / "reports/migration-03" / ("docker-" + uuid.uuid4().hex[:10])
        run.mkdir(parents=True)
        benchmark = fixture(run / "tasks")
        task = run / "tasks/synthetic"
        stage_benchmark(benchmark, task, {})
        env = {**os.environ, "MIGRATION_CREDENTIAL_SENTINEL": CREDENTIAL.decode()}
        observed = []
        oracle = (task / "solution/solve.sh").read_text()
        attacks = {
            "oracle": oracle,
            "symlink": "#!/bin/sh\nln -s /solution/config.yaml /app/submission/config.yaml\n",
            "executable": oracle + "chmod +x /app/submission/config.yaml\n",
            "extra": oracle + "echo bad > /app/submission/evil.sh\n",
            "traversal": "#!/bin/sh\n! echo forbidden > /app/submission/../../submission/config.yaml\n",
        }
        for case in ("nop", *attacks):
            agent = "nop" if case == "nop" else "oracle"
            if case != "nop":
                (task / "solution/solve.sh").write_text(
                    attacks[case]
                    + "test ! -w /opt/sapi-submission.py\ntest ! -r /submission\n"
                    + "mkdir -p /logs/artifacts/nested\necho undeclared > /logs/artifacts/nested/evil.sh\n"
                    + "echo hidden > /logs/artifacts/.hidden\nln -s /tests/test.sh /logs/artifacts/link\n"
                )
            command = [
                str(ROOT / ".venv/bin/harbor"),
                "run",
                "--path",
                str(task),
                "--agent",
                agent,
                "--jobs-dir",
                str(run / "jobs"),
                "--job-name",
                case,
                "--n-concurrent",
                "1",
                "--max-retries",
                "0",
                "--no-delete",
                "--ek",
                "keep_containers=true",
            ]
            (run / (case + "-command.json")).write_text(json.dumps(command))
            with (run / (case + ".log")).open("w") as log:
                result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            self.assertEqual(result.returncode, 0, (run / (case + ".log")).read_text())
            trials = list((run / "jobs" / case).glob("*/result.json"))
            self.assertEqual(len(trials), 1)
            result = json.loads(trials[0].read_text())
            self.assertIsNone(result["exception_info"], result)
            self.assertEqual(result["verifier_result"]["rewards"]["reward"], int(case == "oracle"))
            placement = json.loads((trials[0].parent / "verifier/evidence/placement.json").read_text())
            self.assertEqual(placement["convention"], [])
            self.assertEqual(placement["evaluator"], {"expected": 7})
            self.assertEqual(placement["private"], PRIVATE.decode())
            containers = subprocess.check_output(["docker", "ps", "-a", "--format", "{{.ID}} {{.Names}}"], text=True)
            names = [
                line.split()[0] for line in containers.splitlines() if trials[0].parent.name.lower() in line.lower()
            ]
            self.assertEqual(len(names), 2, containers)
            try:
                inspection = json.loads(subprocess.check_output(["docker", "inspect", *names]))
                (run / (case + "-inspect.json")).write_text(json.dumps(inspection, indent=2))
                observed.extend(inspection)
                public = next(item for item in inspection if "__verifier__" not in item["Name"])
                verifier = next(item for item in inspection if "__verifier__" in item["Name"])
                self.assertNotEqual(public["Image"], verifier["Image"])
                self.assertEqual({mount["Destination"] for mount in verifier["Mounts"]}, {"/logs/verifier"})
                for secret in (PRIVATE, REFERENCE, CREDENTIAL):
                    self.assertNotIn(secret.decode(), json.dumps(public["Config"]))
                self.assertEqual(
                    {mount["Destination"] for mount in public["Mounts"]},
                    {"/logs/agent", "/logs/verifier", "/logs/artifacts"},
                )
                if case == "nop":
                    image = public["Image"]
                    subprocess.run(["docker", "image", "save", "-o", str(run / "public-image.tar"), image], check=True)
                    with tarfile.open(run / "public-image.tar") as archive:
                        manifest = json.load(archive.extractfile("manifest.json"))[0]
                        layers = []
                        for name in manifest["Layers"]:
                            raw = archive.extractfile(name).read()
                            for secret in (PRIVATE, REFERENCE, CREDENTIAL):
                                self.assertNotIn(secret, raw)
                            with tarfile.open(fileobj=archive.extractfile(name), mode="r|*") as layer:
                                count = inspect_tar(layer)
                            layers.append(
                                {
                                    "path": name,
                                    "sha256": hashlib.sha256(raw).hexdigest(),
                                    "bytes": len(raw),
                                    "files": count,
                                }
                            )
                        (run / "layer-inspection.json").write_text(
                            json.dumps({"image": image, "layers": layers}, indent=2)
                        )
                    subprocess.run(
                        ["docker", "export", "-o", str(run / "public-filesystem.tar"), public["Id"]], check=True
                    )
                    with tarfile.open(run / "public-filesystem.tar") as archive:
                        inspect_tar(archive)
            finally:
                subprocess.run(["docker", "rm", "-f", *names], check=True, capture_output=True)
                for item in inspection:
                    project = item["Config"]["Labels"]["com.docker.compose.project"]
                    networks = subprocess.check_output(
                        ["docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project=" + project],
                        text=True,
                    ).split()
                    if networks:
                        subprocess.run(["docker", "network", "rm", *networks], check=True, capture_output=True)
        (task / "tests/payload/dependencies/family/rules.py").unlink()
        missing = subprocess.run(["docker", "build", str(task / "tests")], capture_output=True, timeout=180)
        (run / "missing-dependency.log").write_bytes(missing.stdout + missing.stderr)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn(b"ModuleNotFoundError", missing.stdout + missing.stderr)
        (run / "result.json").write_text(
            json.dumps({"status": "passed", "containers": [item["Name"] for item in observed]}, indent=2)
        )
