"""Check generic packages and each declared benchmark's independent Python namespace."""

import subprocess
import sys

from sapi_config_lab.benchmark import discover_benchmarks
from sapi_config_lab.paths import workspace_root


def main() -> int:
    root = workspace_root()
    targets = [("src", "verification")]
    targets.extend(
        (str(benchmark.directory),)
        for benchmark in discover_benchmarks(root / "benchmarks")
        if any(item.source.suffix == ".py" for item in benchmark.files)
    )
    failed = False
    for target in targets:
        failed |= subprocess.run([sys.executable, "-m", "mypy", *target], cwd=root, check=False).returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
