"""Build disposable archives and reject private evidence in either distribution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from sapi_config_lab.benchmark import discover_benchmarks
from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root

PRIVATE_PATHS = (
    "reports/future-run/report.json",
    "generated/future-export.json",
    "validation/future-check.json",
    "review/future-review.md",
    "reviews/future-review.md",
    ".scratch/future-note.md",
    "provenance/environment.json",
    "provenance/future-local-snapshot.json",
    ".env",
    "src/sapi_config_lab/.env.private",
)


def shared_fixture(root: Path) -> None:
    """Exercise declared shared resources without adding a production benchmark."""
    source = next(item for item in discover_benchmarks(root / "benchmarks") if item.name == "invoice-total")
    directory = root / "benchmarks/distribution-shared"
    shutil.copytree(source.directory, directory)
    metadata = json.loads((directory / "scenario.json").read_text())
    metadata.update(id="distribution-shared", default=False)
    metadata["dependencies"] = {
        "sample": {"path": "_shared/distribution", "public": ["public.txt"], "trusted": ["rules.py"]}
    }
    (directory / "scenario.json").write_text(json.dumps(metadata))
    shared = root / "benchmarks/_shared/distribution"
    shared.mkdir(parents=True)
    (shared / "public.txt").write_text("DECLARED_PUBLIC_DEPENDENCY\n")
    (shared / "rules.py").write_text("EXPECTED = 'DECLARED_PRIVATE_DEPENDENCY'\n")
    evaluator = directory / "evaluation/evaluator.py"
    evaluator.write_text("from ..dependencies.sample.rules import EXPECTED\n" + evaluator.read_text())
    (shared / ".env").write_text("PRIVATE_EVIDENCE_SENTINEL\n")


INSTALLED_PROBE = """
import hashlib, json
from pathlib import Path
import subprocess, sys
from sapi_config_lab.benchmark import discover_benchmarks
from sapi_config_lab.coordinate.benchmark_packages import CORE_FILES, VERIFIER_FILES
from sapi_config_lab.coordinate.packages import stage_tasks
from sapi_config_lab.paths import resource_root, workspace_root
root = resource_root()
assert not (root / 'src').exists()
try:
    workspace_root()
except RuntimeError:
    pass
else:
    raise AssertionError('installed runtime admitted an editable experiment')
expected = json.loads(Path(sys.argv[1]).read_text())
for relative, digest in expected.items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest, relative
items = discover_benchmarks(root / 'benchmarks')
assert {item.name for item in items} == {'invoice-total', 'checkout-recovery', 'distribution-shared'}
cli = [sys.executable, '-I', '-m', 'sapi_config_lab']
subprocess.run([*cli, 'benchmarks'], check=True, stdout=subprocess.DEVNULL)
for item in items:
    subprocess.run([*cli, 'compile', str(item.reference.source), '--scenario', item.name,
                    '--output', str(Path.cwd() / (item.name + '.json'))], check=True, stdout=subprocess.DEVNULL)
destination = Path.cwd() / 'tasks'
subprocess.run([*cli, 'package-tasks', str(destination), '--scenario', 'invoice-total',
                '--scenario', 'checkout-recovery', '--scenario', 'distribution-shared'], check=True,
               stdout=subprocess.DEVNULL)
for item in items:
    task = destination / item.name
    for declared in item.files:
        if declared.visibility == 'reference':
            assert (task / 'solution/config.yaml').read_bytes() == declared.source.read_bytes()
        else:
            assert (task / 'tests/payload' / declared.destination).read_bytes() == declared.source.read_bytes()
            public = task / 'environment/payload' / declared.destination
            if declared.visibility == 'public':
                assert public.read_bytes() == declared.source.read_bytes()
            else:
                assert not public.exists(), declared.destination
    for relative in CORE_FILES:
        assert (task / 'tests/core/sapi_config_lab' / relative).is_file(), relative
    for relative in VERIFIER_FILES:
        assert (task / 'tests/core/verification' / relative).is_file(), relative
    tests = task / 'tests'
    code = 'import sys; sys.path[:0] = ' + repr([str(tests / 'core'), str(tests)]) + '; exec(' + repr((tests / 'check_imports.py').read_text()) + ')'
    subprocess.run([sys.executable, '-I', '-c', code], check=True)
stage_tasks(Path.cwd() / 'generation', root=root, benchmarks=items, mode='generation')
print(json.dumps({'installed': True, 'staged': [item.name for item in items], 'declared_resources': len(expected)}))
"""


def inspect_installation(wheel: Path, work: Path, requirements: Path, expected: Path) -> None:
    """Probe a fresh installation outside checkout paths and inherited Python settings."""
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "SAPI_LAB_ROOT", "VIRTUAL_ENV"}
    }
    subprocess.run(["uv", "venv", "--python", sys.executable, str(work / "venv")], env=environment, check=True)
    python = work / "venv/bin/python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "-r", str(requirements), str(wheel)],
        env=environment,
        check=True,
    )
    outside = work / "outside"
    outside.mkdir()
    subprocess.run([str(python), "-I", "-c", INSTALLED_PROBE, str(expected)], cwd=outside, env=environment, check=True)


def main() -> int:
    root = workspace_root()
    with tempfile.TemporaryDirectory(prefix="sapi-distribution-") as temporary:
        work = Path(temporary)
        checkout = work / "checkout"
        checkout.mkdir()
        for relative in source_manifest():
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        for relative in PRIVATE_PATHS:
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("PRIVATE_EVIDENCE_SENTINEL\n")
        shared_fixture(checkout)
        subprocess.run(
            ["uv", "build", "--python", sys.executable, "--out-dir", str(work / "dist"), str(checkout)],
            check=True,
        )
        sdist = next((work / "dist").glob("*.tar.gz"))
        wheel = next((work / "dist").glob("*.whl"))
        with tarfile.open(sdist) as archive:
            source_members = {"/".join(Path(member.name).parts[1:]) for member in archive if member.isfile()}
        with zipfile.ZipFile(wheel) as archive:
            wheel_members = set(archive.namelist())
            if any(b"PRIVATE_EVIDENCE_SENTINEL" in archive.read(name) for name in wheel_members):
                raise RuntimeError("Private sentinel leaked into wheel")
        forbidden = set(PRIVATE_PATHS)
        leaked = forbidden & source_members
        leaked |= {name for name in wheel_members if name.endswith(".env.private")}
        if leaked:
            raise RuntimeError(f"Private files leaked into a distribution: {sorted(leaked)}")
        for required in (
            "provenance/SapiensSpecNotation.hs",
            "provenance/spec-comparison.json",
            "provenance/spec-source.json",
            ".python-version",
            ".dockerignore",
            "uv.lock",
        ):
            if required not in source_members:
                raise RuntimeError(f"Missing reproducibility source: {required}")
        expected_resources = {}
        for benchmark in discover_benchmarks(checkout / "benchmarks"):
            declared = (benchmark.directory / "scenario.json", *(item.source for item in benchmark.files))
            for source in declared:
                required = source.relative_to(checkout).as_posix()
                if required not in source_members:
                    raise RuntimeError(f"Missing declared benchmark dependency: {required}")
                installed = "sapi_config_lab/resources/" + required
                if installed not in wheel_members:
                    raise RuntimeError(f"Missing installed benchmark dependency: {required}")
                expected_resources[required] = hashlib.sha256(source.read_bytes()).hexdigest()
        for name in ("FORMAT.md", "PROFILE.md"):
            relative = "generation/" + name
            expected_resources[relative] = hashlib.sha256((checkout / relative).read_bytes()).hexdigest()
        for required in (
            "sapi_config_lab/execute/agency-prompt.md",
            "sapi_config_lab/compile/runtime-fragment.js",
            "sapi_config_lab/harbor_integration/runtime/Dockerfile",
            "sapi_config_lab/harbor_integration/submission.py",
            "verification/__init__.py",
            "verification/fixture.py",
            "verification/verify.py",
        ):
            if required not in wheel_members:
                raise RuntimeError(f"Missing installed runtime resource: {required}")
        unexpected = [
            name
            for name in wheel_members
            if not name.startswith(("sapi_config_lab/", "sapi_config_lab-", "verification/"))
        ]
        if unexpected:
            raise RuntimeError(f"Unexpected wheel entries: {unexpected}")
        expected = work / "resources.json"
        expected.write_text(json.dumps(expected_resources))
        requirements = work / "requirements.txt"
        with requirements.open("w") as stream:
            subprocess.run(
                [
                    "uv",
                    "export",
                    "--project",
                    str(checkout),
                    "--locked",
                    "--no-dev",
                    "--extra",
                    "harbor",
                    "--extra",
                    "benchmark",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                ],
                stdout=stream,
                check=True,
            )
        inspect_installation(wheel, work / "from-sdist", requirements, expected)
        subprocess.run(
            ["uv", "build", "--wheel", "--python", sys.executable, "--out-dir", str(work / "direct"), str(checkout)],
            check=True,
        )
        inspect_installation(next((work / "direct").glob("*.whl")), work / "direct-wheel", requirements, expected)
        print(
            json.dumps(
                {
                    "status": "passed",
                    "private_sentinels_excluded": len(PRIVATE_PATHS),
                    "sdist_files": len(source_members),
                    "wheel_files": len(wheel_members),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
