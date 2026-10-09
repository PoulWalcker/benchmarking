"""Check generic code and each native task's independent Python namespace."""

import subprocess
import sys

from sapi_config_lab.paths import workspace_root


def main() -> int:
    root = workspace_root()
    targets: list[tuple[str, ...]] = [("src", "verification")]
    for manifest in sorted((root / "tasks").glob("*/task.toml")):
        task = manifest.parent
        sources = [task / "experiment.py"]
        sources.extend(path for area in ("evaluation", "environment") for path in (task / area).rglob("*.py"))
        target = tuple(str(path) for path in sorted(sources) if path.is_file())
        if target:
            targets.append(target)
    failed = False
    for target in targets:
        failed |= subprocess.run([sys.executable, "-m", "mypy", *target], cwd=root, check=False).returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
