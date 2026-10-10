"""Select checked-in Harbor tasks and their native experiment policy."""

from collections.abc import Iterable
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import tomllib

from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import resource_root


def policy(task: Path) -> dict:
    """Read policy values from native TOML; executable paths are never configuration."""
    value = tomllib.loads((task / "task.toml").read_text()).get("metadata", {}).get("sapi", {})
    allowed = {
        "default",
        "authoring_attempts",
        "runtime_model_calls",
        "judge_calls",
        "reference_reward",
        "catalogs",
        "admission",
        "human_review",
    }
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
    if value.get("admission", "evaluate") not in {"compile", "evaluate"}:
        raise ValueError("Unknown native admission policy")
    if "human_review" in value and type(value["human_review"]) is not bool:
        raise ValueError("Native human review policy must be boolean")
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


def public_sources(task: Path) -> tuple[str, ...]:
    """Reject unsafe task image recipes and contexts, returning declared public files in order."""
    errors: list[str] = []

    def reject(rule: str, detail: str) -> None:
        errors.append(f"{task.name}: {rule}: {detail}")

    def read(name: str, rule: str) -> str:
        path = task / name
        try:
            if not stat.S_ISREG(path.lstat().st_mode):
                reject(rule, f"{name} must be a regular non-symlink file")
                return ""
            value = path.read_bytes().decode("utf-8")
        except UnicodeError as error:
            reject("S3" if rule == "S1" else rule, f"{name}: {error}")
            return ""
        except OSError as error:
            reject(rule, f"{name}: {error}")
            return ""
        if "\r" in value:
            reject(rule, f"{name} contains CR characters")
        return value

    recipe = read("images.Dockerfile", "S1")
    if "\r" in recipe:
        reject("S3", "recipe must use LF line endings")
    ignore = read("images.Dockerfile.dockerignore", "I1")
    exclusions = {line.strip() for line in ignore.splitlines()}
    required = {
        "**/__pycache__",
        "**/*.pyc",
        "**/.DS_Store",
        "**/.env",
        "**/.env.*",
        "**/*.log",
        "**/.pytest_cache",
        "**/.mypy_cache",
        "**/.ruff_cache",
    }
    for missing in sorted(required - exclusions):
        reject("I2", f"missing exact exclusion {missing}")
    for line in sorted(exclusions):
        if line.startswith("!"):
            reject("I3", f"negation is forbidden: {line}")

    first = "FROM sapi-native-public-base:phase1 AS public"
    second = "FROM sapi-native-core:phase1 AS verifier"
    physical = recipe.splitlines()
    if next((line for line in physical if line.strip()), "") != first:
        reject("S1", f"first non-blank line must be {first}")
    lines: list[str] = []
    pending = ""
    for line in physical:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pending += line
        if pending.endswith("\\"):
            pending = pending[:-1] + " "
            continue
        lines.append(pending)
        pending = ""
    if pending:
        reject("S3", "unfinished continuation at EOF")
    stages = [line for line in lines if line.split()[0].upper() == "FROM"]
    if stages != [first, second]:
        reject("S2", "exactly public then verifier FROM lines are required")

    sources: list[str] = []
    basenames: set[str] = set()
    stage = ""
    verifier: list[str] = []
    for line in lines:
        words = line.split()
        if words[0].upper() == "FROM":
            stage = "public" if line == first else "verifier"
            if re.match(r"FROM\s+public(?:\s|$)", line, re.IGNORECASE):
                reject("S8", "verifier cannot inherit public")
            continue
        if stage == "verifier":
            verifier.append(line)
            if re.search(r"--from(?:=|\s+)public(?:\s|$)", line, re.IGNORECASE):
                reject("S8", "verifier cannot copy from public")
            continue
        if stage != "public":
            continue
        if words[0] != "COPY" or "<<" in line or (len(words) > 1 and words[1].startswith("[")):
            reject("S3", f"unsupported public instruction: {line}")
            continue
        if any(word.startswith("--") for word in words[1:]):
            reject("S4", f"public COPY flags are forbidden: {line}")
        if len(words) < 3 or words[-1] != "/app/public/":
            reject("S6", f"COPY destination must be /app/public/: {line}")
        for source in words[1:-1]:
            allowed = source in {"instruction.md", "task.md", "bindings.yaml"} or bool(
                re.fullmatch(r"public/[A-Za-z0-9][A-Za-z0-9._-]*", source) and ".." not in source
            )
            if not allowed:
                reject("S5", f"source is not allowlisted: {source}")
                continue
            basename = source.rsplit("/", 1)[-1]
            if source in sources or basename in basenames:
                reject("S5", f"duplicate source or destination basename: {source}")
            sources.append(source)
            basenames.add(basename)
            try:
                if not stat.S_ISREG((task / source).lstat().st_mode):
                    reject("S7", f"source must be a regular non-symlink file: {source}")
            except OSError as error:
                reject("S7", f"{source}: {error}")
    if "instruction.md" not in sources:
        reject("S5", "instruction.md is required")
    if not verifier or not re.fullmatch(r'RUN python3 -c "from .+"', verifier[-1]):
        reject("S8", "last verifier instruction must be a python3 import check")

    public = task / "public"
    try:
        mode = public.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as error:
        reject("S7", f"public/: {error}")
        mode = None
    if mode is not None:
        if not stat.S_ISDIR(mode):
            reject("S7", "public/ must be a real directory")
        else:
            try:
                for path in sorted(public.iterdir()):
                    if not stat.S_ISREG(path.lstat().st_mode):
                        reject("S7", f"public/ contains a non-regular file: {path.name}")
                    if f"public/{path.name}" not in sources:
                        reject("S7", f"undeclared public file: {path.name}")
            except OSError as error:
                reject("S7", f"public/: {error}")
    if errors:
        raise ValueError("\n".join(errors))
    return tuple(sources)


def require_verifier_phase(task: Path, seconds: int) -> None:
    """Admit the task's computed observation ceiling without overriding Harbor's phase."""
    limit = tomllib.loads((task / "task.toml").read_text())["verifier"]["timeout_sec"]
    if seconds > limit:
        raise ValueError("Planned observations exceed the native verifier phase")


def invoke(task: Path, request: dict, sources: dict) -> dict:
    """Run the fixed task-owned trusted composition after verifying all source bytes."""
    root = resource_root()
    if task not in select_tasks(root / "tasks", [task.name]) or source_manifest() != sources:
        raise ValueError("Native task invocation source mismatch")
    timeout = 420 if request.get("action") == "evaluate" else 300
    with tempfile.TemporaryDirectory(prefix="sapi-task-imports-") as cache:
        bootstrap = "import runpy,sys; sys.path[:0]=sys.argv[1:4]; runpy.run_path(sys.argv[4],run_name='__main__')"
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                "pycache_prefix=" + cache,
                "-c",
                bootstrap,
                str(Path(__file__).resolve().parents[2]),
                str(root),
                str(task),
                str(task / "experiment.py"),
            ],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            cwd=root,
        )
    if result.returncode == 124:
        raise subprocess.TimeoutExpired(result.args, timeout)
    result.check_returncode()
    if source_manifest() != sources:
        raise ValueError("Native task invocation sources changed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("Native task result must be an object")
    return value
