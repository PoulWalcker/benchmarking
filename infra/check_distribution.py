"""Build disposable archives and reject private evidence in either distribution."""

from __future__ import annotations

import json
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
        for benchmark in discover_benchmarks(root / "benchmarks"):
            declared = (benchmark.directory / "scenario.json", *(item.source for item in benchmark.files))
            for source in declared:
                required = source.relative_to(root).as_posix()
                if required not in source_members:
                    raise RuntimeError(f"Missing declared benchmark dependency: {required}")
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
