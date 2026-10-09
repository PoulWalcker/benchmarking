"""Check ordinary Python distributions without installing benchmark task resources."""

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
    "tasks/invoice-total/.env",
)
RUNTIME_FILES = (
    "sapi_config_lab/coordinate/fixture-judge-prompt.md",
    "sapi_config_lab/execute/agency-prompt.md",
    "sapi_config_lab/compile/runtime-fragment.js",
    "sapi_config_lab/harbor_integration/runtime/Dockerfile",
    "sapi_config_lab/harbor_integration/submission.py",
    "verification/__init__.py",
    "verification/fixture.py",
    "verification/verify.py",
)

INSTALLED_PROBE = """
import hashlib, json, shutil, subprocess, sys
from pathlib import Path
import sapi_config_lab, verification
from sapi_config_lab.compile import n8n
from sapi_config_lab.execute import n8n as runtime
from sapi_config_lab.evaluate.records import validate_result
from sapi_config_lab.paths import workspace_root
from verification.fixture import FixtureEvaluator
from verification import verify
core = Path(sapi_config_lab.__file__).parent
assert not (core / 'resources').exists()
try:
    workspace_root()
except RuntimeError:
    pass
else:
    raise AssertionError('installed runtime admitted an editable experiment')
expected = json.loads(Path(sys.argv[1]).read_text())
for relative, digest in expected.items():
    assert hashlib.sha256((core.parent / relative).read_bytes()).hexdigest() == digest, relative
for name in ('config.yaml', 'bindings.yaml', 'operations.js'):
    shutil.copyfile(Path(sys.argv[2]) / name, name)
cli = [sys.executable, '-I', '-m', 'sapi_config_lab']
compiled = subprocess.run([*cli, 'compile', 'config.yaml', '--bindings', 'bindings.yaml',
                           '--operations', 'operations.js', '--output', 'compiled.json',
                           '--llm-mode', 'live', '--bridge-url', 'http://127.0.0.1:1'],
                          text=True, capture_output=True, check=True)
assert json.loads(compiled.stdout)['executed'] is False
assert json.loads(Path('compiled.json').read_text())['nodes']
assert 'classify' in json.loads(Path('compiled.map.json').read_text())
assert validate_result({'execution': None, 'acceptance': None, 'quality': None})['quality'] is None
print(json.dumps({'installed': True, 'detached_compile': True, 'runtime_resources': len(expected)}))
"""


def inspect_installation(wheel: Path, work: Path, requirements: Path, expected: Path, inputs: Path) -> None:
    """Compile explicit inputs from a clean base-dependency install outside the checkout."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTHON") and key not in {"SAPI_LAB_ROOT", "VIRTUAL_ENV"}
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
    subprocess.run(
        [str(python), "-I", "-c", INSTALLED_PROBE, str(expected), str(inputs)],
        cwd=outside,
        env=environment,
        check=True,
    )


def main() -> int:
    root = workspace_root()
    with tempfile.TemporaryDirectory(prefix="sapi-distribution-") as temporary:
        work = Path(temporary).resolve()
        checkout = work / "checkout"
        checkout.mkdir()
        for relative in source_manifest():
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
        sentinel = b"PRIVATE_EVIDENCE_SENTINEL:" + os.urandom(16).hex().encode()
        for relative in PRIVATE_PATHS:
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(sentinel + b"\n")
        subprocess.run(
            ["uv", "build", "--python", sys.executable, "--out-dir", str(work / "dist"), str(checkout)],
            check=True,
        )
        sdist = next((work / "dist").glob("*.tar.gz"))
        wheel = next((work / "dist").glob("*.whl"))
        with tarfile.open(sdist) as archive:
            source_members = {"/".join(Path(member.name).parts[1:]) for member in archive if member.isfile()}
            for member in archive:
                if member.isfile():
                    archived_file = archive.extractfile(member)
                    assert archived_file is not None
                    if sentinel in archived_file.read():
                        raise RuntimeError("Private sentinel leaked into source distribution")
        with zipfile.ZipFile(wheel) as archive:
            wheel_members = set(archive.namelist())
            if any(sentinel in archive.read(name) for name in wheel_members):
                raise RuntimeError("Private sentinel leaked into wheel")
        leaked = set(PRIVATE_PATHS) & source_members
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
        for required in RUNTIME_FILES:
            if required not in wheel_members:
                raise RuntimeError(f"Missing installed runtime resource: {required}")
            source = checkout / ("src" if required.startswith("sapi_config_lab/") else "") / required
            expected_resources[required] = hashlib.sha256(source.read_bytes()).hexdigest()
        unexpected = [
            name
            for name in wheel_members
            if not name.startswith(("sapi_config_lab/", "sapi_config_lab-", "verification/"))
            or name.startswith("sapi_config_lab/resources/")
        ]
        if unexpected:
            raise RuntimeError(f"Unexpected wheel entries: {unexpected}")
        expected = work / "resources.json"
        expected.write_text(json.dumps(expected_resources))
        inputs = root / "tests/support/native-transport/tests"
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
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                ],
                stdout=stream,
                check=True,
            )
        inspect_installation(wheel, work / "from-sdist", requirements, expected, inputs)
        subprocess.run(
            ["uv", "build", "--wheel", "--python", sys.executable, "--out-dir", str(work / "direct"), str(checkout)],
            check=True,
        )
        inspect_installation(
            next((work / "direct").glob("*.whl")), work / "direct-wheel", requirements, expected, inputs
        )
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
