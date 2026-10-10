"""Task-owned recipe validation and fail-closed Docker build admission."""

import fnmatch
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

from sapi_config_lab.coordinate import native_tasks
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
IGNORE = """**/__pycache__
**/*.pyc
**/.DS_Store
**/.env
**/.env.*
**/*.log
**/.pytest_cache
**/.mypy_cache
**/.ruff_cache
"""
RECIPE = """FROM sapi-native-public-base:phase1 AS public
COPY instruction.md bindings.yaml /app/public/
FROM sapi-native-core:phase1 AS verifier
COPY evaluation /tests/payload/evaluation
RUN python3 -c "from payload.evaluation.evaluator import evaluate"
"""


def fixture_task(root: Path, name: str = "fixture") -> Path:
    """Create an isolated native definition with a deliberately small public surface."""
    task = root / name
    task.mkdir(parents=True)
    (task / "task.toml").write_text("[metadata.sapi]\ndefault = false\n")
    (task / "instruction.md").write_text("Public instruction\n")
    (task / "bindings.yaml").write_text("{}\n")
    (task / "images.Dockerfile").write_text(RECIPE)
    (task / "images.Dockerfile.dockerignore").write_text(IGNORE)
    return task


class PublicSourcesTests(unittest.TestCase):
    def test_discovered_tasks_declare_public_sources_in_recipe_order(self):
        tasks = native_tasks.select_tasks(
            ROOT / "tasks", sorted(p.parent.name for p in (ROOT / "tasks").glob("*/task.toml"))
        )
        for task in tasks:
            with self.subTest(task=task.name):
                sources = native_tasks.public_sources(task)
                self.assertIn("instruction.md", sources)
                if task.name == "checkout-recovery":
                    self.assertEqual(
                        sources,
                        ("instruction.md", "public/authoring-notes.md", "bindings.yaml", "public/authoring-prompt.txt"),
                    )

    def test_recipe_violations_fail_closed_with_rule_ids(self):
        cases = [
            ("S5", RECIPE.replace("instruction.md bindings.yaml", source))
            for source in (
                ".",
                "public/",
                "public/*.md",
                "../x",
                "evaluation/evaluator.py",
                "solution/config.yaml",
                "notes.md",
                "public/.hidden",
                "public/-x",
                "public/a..md",
            )
        ] + [
            ("S1", "# syntax=docker/dockerfile:1\n" + RECIPE),
            ("S1", "# escape=`\n" + RECIPE),
            ("S1", "# comment\n" + RECIPE),
            ("S1", "ARG BASE\n" + RECIPE),
            ("S1", " " + RECIPE),
            ("S2", RECIPE.replace("FROM sapi-native-core", " FROM sapi-native-core")),
            ("S2", RECIPE + "FROM alpine AS extra\n"),
            ("S2", RECIPE.replace("FROM sapi-native-core:phase1 AS verifier", "FROM public AS verifier")),
            (
                "S3",
                RECIPE.replace(
                    "COPY instruction.md bindings.yaml /app/public/", 'COPY ["instruction.md", "/app/public/"]'
                ),
            ),
            ("S3", RECIPE.replace("COPY instruction.md", "ADD instruction.md")),
            ("S3", RECIPE.replace("COPY instruction.md", "RUN instruction.md")),
            ("S3", RECIPE.replace("COPY instruction.md", "COPY <<EOF instruction.md")),
            ("S3", RECIPE.replace("\n", "\r\n")),
            ("S3", RECIPE + "RUN \\"),
            ("S4", RECIPE.replace("COPY instruction.md", "COPY --from=verifier instruction.md")),
            ("S4", RECIPE.replace("COPY instruction.md", "COPY --chown=1000 instruction.md")),
            ("S5", RECIPE.replace("instruction.md bindings.yaml", "bindings.yaml")),
            ("S5", RECIPE.replace("instruction.md bindings.yaml", "instruction.md instruction.md")),
            ("S6", RECIPE.replace("/app/public/", "/app/public")),
            ("S8", RECIPE[: RECIPE.index("RUN python3")]),
            ("S8", RECIPE.replace("COPY evaluation", "COPY --from=public evaluation")),
            ("S8", RECIPE + "ENV X=y\n"),
        ]
        for rule, recipe in cases:
            with self.subTest(rule=rule, recipe=recipe), tempfile.TemporaryDirectory() as temporary:
                task = fixture_task(Path(temporary))
                (task / "images.Dockerfile").write_bytes(recipe.encode())
                with self.assertRaisesRegex(ValueError, f"fixture: {rule}:"):
                    native_tasks.public_sources(task)

    def test_sources_are_regular_declared_files_in_a_flat_public_directory(self):
        for case, rule in (
            ("symlink-file", "S7"),
            ("symlink-directory", "S7"),
            ("subdirectory", "S7"),
            ("undeclared", "S7"),
            ("duplicate-basename", "S5"),
            ("missing", "S7"),
            ("root-symlink", "S7"),
            ("fifo", "S7"),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                task = fixture_task(Path(temporary))
                public = task / "public"
                public.mkdir()
                (public / "x.md").write_text("Extra public file\n")
                (task / "images.Dockerfile").write_text(
                    RECIPE.replace("bindings.yaml /app/public/", "bindings.yaml public/x.md /app/public/")
                )
                if case == "symlink-file":
                    (public / "x.md").unlink()
                    (public / "x.md").symlink_to(task / "instruction.md")
                elif case == "symlink-directory":
                    public.rename(task / "other")
                    public.symlink_to(task / "other", target_is_directory=True)
                elif case == "subdirectory":
                    (public / "sub").mkdir()
                    (public / "sub/x.md").write_text("Nested\n")
                elif case == "undeclared":
                    (public / "extra.md").write_text("Undeclared\n")
                elif case == "duplicate-basename":
                    (public / "bindings.yaml").write_text("{}\n")
                    (task / "images.Dockerfile").write_text(
                        RECIPE.replace(
                            "bindings.yaml /app/public/", "bindings.yaml public/x.md public/bindings.yaml /app/public/"
                        )
                    )
                elif case == "missing":
                    (public / "x.md").unlink()
                elif case == "root-symlink":
                    (task / "bindings.yaml").unlink()
                    (task / "bindings.yaml").symlink_to(task / "instruction.md")
                elif case == "fifo":
                    (public / "x.md").unlink()
                    os.mkfifo(public / "x.md")
                with self.assertRaisesRegex(ValueError, f"fixture: {rule}:"):
                    native_tasks.public_sources(task)

    def test_missing_symlinked_and_non_utf8_recipes_are_rejected(self):
        for case, rule in (("missing", "S1"), ("symlink", "S1"), ("non-utf8", "S3")):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                task = fixture_task(Path(temporary))
                path = task / "images.Dockerfile"
                if case == "non-utf8":
                    path.write_bytes(b"\xff")
                else:
                    path.unlink()
                    if case == "symlink":
                        (task / "other").write_text(RECIPE)
                        path.symlink_to(task / "other")
                with self.assertRaisesRegex(ValueError, f"fixture: {rule}:"):
                    native_tasks.public_sources(task)

    def test_ignore_violations_fail_closed_with_rule_ids(self):
        cases = [("I2", IGNORE.replace(line + "\n", "")) for line in IGNORE.splitlines()]
        cases += [
            ("I2", IGNORE.replace("**/__pycache__", weaker)) for weaker in ("__pycache__", "**/__pycache__/*.pyc")
        ]
        cases += [("I3", IGNORE + line + "\n") for line in ("!**/__pycache__/keep.pyc", "  !evaluation/")]
        cases += [("I1", IGNORE.replace("\n", "\r\n")), ("I1", None), ("I1", b"\xff"), ("I1", "symlink")]
        for rule, content in cases:
            with self.subTest(rule=rule, content=content), tempfile.TemporaryDirectory() as temporary:
                task = fixture_task(Path(temporary))
                path = task / "images.Dockerfile.dockerignore"
                if content is None:
                    path.unlink()
                elif content == "symlink":
                    path.rename(task / "ignore")
                    path.symlink_to(task / "ignore")
                else:
                    path.write_bytes(content if isinstance(content, bytes) else content.encode())
                with self.assertRaisesRegex(ValueError, f"fixture: {rule}:"):
                    native_tasks.public_sources(task)

    def test_all_violations_are_reported_together(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = fixture_task(Path(temporary))
            (task / "images.Dockerfile").write_text(RECIPE.replace("instruction.md bindings.yaml", "."))
            (task / "images.Dockerfile.dockerignore").write_text("!evaluation/\n")
            with self.assertRaises(ValueError) as caught:
                native_tasks.public_sources(task)
            for rule in ("S5", "I2", "I3"):
                self.assertIn(f"fixture: {rule}:", str(caught.exception))

    def test_optional_public_assets_comments_and_continuations_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            task = fixture_task(Path(temporary))
            (task / ".dockerignore").write_text("!evaluation/\n")
            (task / "images.Dockerfile.dockerignore").write_text(
                "# Extra exclusions are safe\n\n" + IGNORE + "private/\n"
            )
            self.assertEqual(native_tasks.public_sources(task), ("instruction.md", "bindings.yaml"))
            (task / "public").mkdir()
            self.assertEqual(native_tasks.public_sources(task), ("instruction.md", "bindings.yaml"))
            (task / "public/x.md").write_text("Extra\n")
            (task / "images.Dockerfile").write_text(
                RECIPE.replace(
                    "instruction.md bindings.yaml",
                    "instruction.md \\\n# Continuation comments are permitted\n bindings.yaml public/x.md",
                )
            )
            self.assertEqual(native_tasks.public_sources(task), ("instruction.md", "bindings.yaml", "public/x.md"))

    def test_shared_dockerfile_has_only_common_base_targets(self):
        recipe = (ROOT / "infra/native/Dockerfile").read_text()
        self.assertNotIn("tasks/", recipe)
        self.assertEqual(
            [line.split()[-1] for line in recipe.splitlines() if line.startswith("FROM ")], ["public", "core"]
        )
        subprocess.run(["sh", "-n", str(ROOT / "infra/native/build.sh")], check=True)


class BuildPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / "infra/native/build.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "infra/native/build.sh", self.script)
        self.task = fixture_task(self.root / "tasks")
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(
            "#!"
            + sys.executable
            + "\nimport os,sys,json\nwith open(os.environ['DOCKER_CALLS'], 'a') as f: f.write(json.dumps({'argv': sys.argv[1:], 'buildkit': os.environ.get('DOCKER_BUILDKIT')}) + '\\n')\n"
        )
        docker.chmod(0o755)
        self.calls = self.root / "docker-calls.jsonl"
        self.env = {
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "DOCKER_CALLS": str(self.calls),
            "DOCKER_BUILDKIT": "0",
            "SAPI_LAB_ROOT": str(ROOT),
        }

    def test_invalid_definitions_stop_before_any_docker_command(self):
        for case, rule, names in (
            ("recipe", "S5", []),
            ("ignore", "I2", []),
            ("negation", "I3", []),
            ("missing", "I1", []),
            ("unknown", "scenario selection", ["unknown"]),
            ("pair", "S5", ["fixture", "other"]),
            ("duplicate", "scenario selection", ["fixture", "fixture"]),
        ):
            with self.subTest(case=case):
                (self.task / "images.Dockerfile").write_text(RECIPE)
                (self.task / "images.Dockerfile.dockerignore").write_text(IGNORE)
                if case == "recipe":
                    (self.task / "images.Dockerfile").write_text(RECIPE.replace("instruction.md bindings.yaml", "."))
                elif case == "ignore":
                    (self.task / "images.Dockerfile.dockerignore").write_text(IGNORE.replace("**/*.pyc\n", ""))
                elif case == "negation":
                    (self.task / "images.Dockerfile.dockerignore").write_text(IGNORE + "!evaluation/\n")
                elif case == "missing":
                    (self.task / "images.Dockerfile.dockerignore").unlink()
                elif case == "pair":
                    other = fixture_task(self.root / "tasks", "other")
                    (other / "images.Dockerfile").write_text(RECIPE.replace("instruction.md bindings.yaml", "."))
                result = subprocess.run(
                    ["sh", str(self.script), *names], env=self.env, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(rule, result.stderr)
                self.assertFalse(self.calls.exists())

    def test_build_uses_validated_names_task_contexts_and_forced_buildkit(self):
        fixture_task(self.root / "tasks", "other")
        for names, expected in ((["fixture"], ["fixture"]), ([], ["fixture", "other"])):
            with self.subTest(names=names):
                self.calls.unlink(missing_ok=True)
                result = subprocess.run(
                    ["sh", str(self.script), *names], env=self.env, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
                self.assertTrue(all(call["buildkit"] == "1" for call in calls))
                self.assertEqual(len(calls), 3 + 2 * len(expected))
                self.assertEqual(
                    [call["argv"][call["argv"].index("-t") + 1] for call in calls[:3]],
                    ["sapi-native-runtime:phase1", "sapi-native-public-base:phase1", "sapi-native-core:phase1"],
                )
                self.assertEqual(
                    [call["argv"] for call in calls[3:]],
                    [
                        [
                            "build",
                            "-f",
                            f"tasks/{name}/images.Dockerfile",
                            "--target",
                            role,
                            "-t",
                            f"sapi-native-{name}-{role}:phase1",
                            f"tasks/{name}",
                        ]
                        for name in expected
                        for role in ("public", "verifier")
                    ],
                )


def image_entries(image: str) -> dict[str, dict]:
    """Read filesystem types, permissions, links and bytes without executing image code."""
    container = subprocess.check_output(["docker", "create", image], text=True).strip()
    try:
        with tempfile.TemporaryFile() as exported:
            subprocess.run(["docker", "export", container], stdout=exported, check=True)
            exported.seek(0)
            result = {}
            with tarfile.open(fileobj=exported) as archive:
                for entry in archive:
                    path = PurePosixPath(entry.name)
                    assert not path.is_absolute() and ".." not in path.parts, entry.name
                    name = "/" + str(path)
                    assert name not in result, f"Duplicate exported path: {name}"
                    digest = None
                    if entry.isfile() or entry.islnk():
                        stream = archive.extractfile(entry)
                        assert stream is not None
                        with stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    # COPY changes directory timestamps; they do not represent added task assets.
                    result[name] = {
                        "type": entry.type.decode("ascii"),
                        "mode": entry.mode,
                        "uid": entry.uid,
                        "gid": entry.gid,
                        "link": entry.linkname,
                        "sha256": digest,
                    }
            return result
    finally:
        subprocess.run(["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL)


def image_digests(tag: str) -> dict[str, str]:
    """Fingerprint image file bytes for verifier hygiene checks."""
    return {path: entry["sha256"] for path, entry in image_entries(tag).items() if entry["sha256"] is not None}


class ImageExportTests(unittest.TestCase):
    def test_export_preserves_nonregular_entries_and_hashes_hardlinked_bytes(self):
        with io.BytesIO() as exported:
            with tarfile.open(fileobj=exported, mode="w") as archive:
                for name, kind, link in (
                    ("app", tarfile.DIRTYPE, ""),
                    ("app/public.txt", tarfile.REGTYPE, ""),
                    ("app/link", tarfile.SYMTYPE, "public.txt"),
                    ("app/hardlink", tarfile.LNKTYPE, "app/public.txt"),
                    ("app/fifo", tarfile.FIFOTYPE, ""),
                ):
                    entry = tarfile.TarInfo(name)
                    entry.type, entry.linkname = kind, link
                    entry.mode, entry.uid, entry.gid = 0o640, 1000, 1000
                    data = b"private bytes" if kind == tarfile.REGTYPE else b""
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data) if data else None)
            payload = exported.getvalue()

        def docker(command, **kwargs):
            if command[1] == "export":
                kwargs["stdout"].write(payload)

        with patch.object(subprocess, "check_output", return_value="container-id\n") as create:
            with patch.object(subprocess, "run", side_effect=docker) as run:
                files = image_entries("sha256:identity")
        create.assert_called_once_with(["docker", "create", "sha256:identity"], text=True)
        self.assertEqual([call.args[0][1] for call in run.call_args_list], ["export", "rm"])
        digest = hashlib.sha256(b"private bytes").hexdigest()
        self.assertEqual(files["/app/public.txt"]["sha256"], digest)
        self.assertEqual(files["/app/hardlink"]["sha256"], digest)
        self.assertEqual(files["/app/hardlink"]["type"], "1")
        self.assertEqual(files["/app/link"]["link"], "public.txt")
        self.assertEqual(files["/app/fifo"]["type"], "6")
        self.assertEqual(files["/app"]["type"], "5")
        self.assertEqual(files["/app/public.txt"]["uid"], 1000)

    def test_failed_export_still_removes_created_container(self):
        def docker(command, **kwargs):
            if command[1] == "export":
                raise subprocess.CalledProcessError(1, command)

        with patch.object(subprocess, "check_output", return_value="container-id\n"):
            with patch.object(subprocess, "run", side_effect=docker) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    image_entries("sha256:identity")
        self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "-f", "container-id"])


def host_artifacts(task: Path) -> dict[str, str]:
    """Fingerprint host artifacts covered by the approved cache and secret exclusion policy."""
    directories = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    patterns = ("*.pyc", ".DS_Store", ".env", ".env.*", "*.log")
    return {
        str(path.relative_to(task)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in task.rglob("*")
        if path.is_file()
        and (
            directories.intersection(path.relative_to(task).parts[:-1])
            or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in patterns)
        )
    }


@unittest.skipUnless(
    os.environ.get("SAPI_RUN_NATIVE_TESTS") == "1", "Set SAPI_RUN_NATIVE_TESTS=1 for unpaid Docker image checks"
)
class NativeImageTests(unittest.TestCase):
    def assert_hygiene(self, files: dict[str, str], artifacts: dict[str, str]) -> None:
        self.assertFalse(
            set(files.values()) & set(artifacts.values()), "Host artifact bytes entered the verifier image"
        )

    def test_verifier_images_exclude_host_artifact_bytes(self):
        reports = ROOT / "reports/native-phase1"
        reports.mkdir(parents=True, exist_ok=True)
        tasks = native_tasks.select_tasks(
            ROOT / "tasks", sorted(p.parent.name for p in (ROOT / "tasks").glob("*/task.toml"))
        )
        for task in tasks:
            with self.subTest(task=task.name):
                native_tasks.public_sources(task)
                artifacts = host_artifacts(task)
                self.assertTrue(
                    any("__pycache__" in path for path in artifacts), "Run on a host with task bytecode caches"
                )
                tag = f"sapi-native-{task.name}-verifier:phase1"
                files = image_digests(tag)
                identity = subprocess.check_output(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
                ).strip()
                leaks = {path: digest for path, digest in files.items() if digest in artifacts.values()}
                (reports / f"{task.name}-verifier-hygiene.json").write_text(
                    json.dumps(
                        {"image": tag, "image_id": identity, "host_artifacts": artifacts, "leaks": leaks}, indent=2
                    )
                    + "\n"
                )
                self.assert_hygiene(files, artifacts)

    def test_dockerfile_specific_ignore_excludes_planted_files_and_detects_its_absence(self):
        tasks = native_tasks.select_tasks(
            ROOT / "tasks", sorted(p.parent.name for p in (ROOT / "tasks").glob("*/task.toml"))
        )
        source = tasks[0]
        native_tasks.public_sources(source)
        nonce = uuid.uuid4().hex
        tags = [f"ticket06-ignore-probe-{nonce}:{variant}" for variant in ("filtered", "unfiltered")]
        reports = ROOT / "reports/native-phase1"
        reports.mkdir(parents=True, exist_ok=True)
        results = {}
        try:
            with tempfile.TemporaryDirectory() as temporary:
                scratch = Path(temporary) / source.name
                shutil.copytree(source, scratch)
                planted = [
                    f"evaluation/__pycache__/planted-{nonce}.pyc",
                    f"evaluation/planted-{nonce}.pyc",
                    "evaluation/.DS_Store",
                ]
                for relative in planted:
                    path = scratch / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes((relative + nonce).encode())
                artifacts = host_artifacts(scratch)
                for variant, tag in zip(("filtered", "unfiltered"), tags, strict=True):
                    if variant == "unfiltered":
                        (scratch / "images.Dockerfile.dockerignore").unlink()
                    with (reports / f"ignore-probe-{variant}.log").open("w") as log:
                        subprocess.run(
                            [
                                "docker",
                                "build",
                                "-f",
                                str(scratch / "images.Dockerfile"),
                                "--target",
                                "verifier",
                                "-t",
                                tag,
                                str(scratch),
                            ],
                            env={**os.environ, "DOCKER_BUILDKIT": "1"},
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=True,
                        )
                    files = image_digests(tag)
                    leaks = {path: digest for path, digest in files.items() if digest in artifacts.values()}
                    if variant == "filtered":
                        self.assert_hygiene(files, artifacts)
                    else:
                        for relative in planted:
                            self.assertIn(artifacts[relative], files.values())
                        with self.assertRaises(AssertionError):
                            self.assert_hygiene(files, artifacts)
                    results[variant] = {
                        "tag": tag,
                        "leaks": leaks,
                        "planted": {path: artifacts[path] for path in planted},
                    }
        finally:
            subprocess.run(["docker", "image", "rm", "-f", *tags], check=False, stdout=subprocess.DEVNULL)
        (reports / "ignore-mechanism-probe.json").write_text(
            json.dumps({"source_task": source.name, "variants": results}, indent=2) + "\n"
        )
