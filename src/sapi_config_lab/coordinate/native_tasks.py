"""Select checked-in Harbor tasks and their native experiment policy."""

from collections.abc import Iterable
from pathlib import Path
import re
import tomllib


def policy(task: Path) -> dict:
    """Read policy values from native TOML; executable paths are never configuration."""
    value = tomllib.loads((task / "task.toml").read_text()).get("metadata", {}).get("sapi", {})
    allowed = {"default", "authoring_attempts", "runtime_model_calls", "judge_calls", "reference_reward", "catalogs"}
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError("Unknown native experiment policy")
    for key in ("authoring_attempts", "runtime_model_calls", "judge_calls"):
        if key in value and (type(value[key]) is not int or value[key] < 0):
            raise ValueError("Native model budgets must be nonnegative integers")
    if "default" in value and type(value["default"]) is not bool:
        raise ValueError("Native default selection must be boolean")
    reward = value.get("reference_reward")
    if reward is not None and (type(reward) not in (float, int) or not 0 <= reward <= 1):
        raise ValueError("Native reference reward must be in 0..1")
    catalogs = value.get("catalogs", ["full"])
    if not isinstance(catalogs, list) or not catalogs or not set(catalogs) <= {"full", "scenario"}:
        raise ValueError("Unknown native prompt catalog")
    return value


def select_tasks(root: Path, names: Iterable[str] | None = None) -> tuple[Path, ...]:
    """Select direct native directories without importing task code or consulting descriptors."""
    available = {path.parent.name: path.parent for path in sorted(root.glob("*/task.toml"))}
    selected = (
        tuple(name for name, path in available.items() if policy(path).get("default"))
        if names is None
        else tuple(names)
    )
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= available.keys():
        raise ValueError("Unknown, duplicate, or empty scenario selection")
    tasks = tuple(available[name] for name in selected)
    if any(task.is_symlink() or not re.fullmatch(r"[a-z][a-z0-9-]*", task.name) for task in tasks):
        raise ValueError("Unsafe native task directory")
    return tasks


def image_tags(tasks: tuple[Path, ...]) -> tuple[str, ...]:
    """Identify the explicit local bases consumed by the immutable task Dockerfiles."""
    tags = set()
    for task in tasks:
        for area in ("environment", "tests"):
            lines = (task / area / "Dockerfile").read_text().splitlines()
            bases = [line.split()[1] for line in lines if line.startswith("FROM ")]
            if len(bases) != 1 or not bases[0].startswith("sapi-native-"):
                raise ValueError("Native task must use one explicitly built local base image")
            tags.update(bases)
    return tuple(sorted(tags))
