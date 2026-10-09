"""Re-evaluate recorded evidence through an explicitly supplied frozen checkout."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import sysconfig
import tempfile

from sapi_config_lab.evaluate.records import validate_result

# Isolated startup excludes editable-install hooks, user site, PYTHONPATH and stale
# checkout bytecode. Only captured source and installed third-party dependencies enter.
_PROGRAM = """
import json, os, sys
from pathlib import Path
root, original, manifest, dependencies, arguments, native_task = json.loads(sys.argv[1])
sys.path[:0] = [str(Path(root) / 'src'), root, *dependencies]
os.environ['SAPI_LAB_ROOT'] = root
from sapi_config_lab.coordinate.provenance import source_manifest
expected = json.loads(Path(manifest).read_text())
if source_manifest(Path(original)) != expected or source_manifest(Path(root)) != expected:
    raise ValueError('Archived source manifest is incomplete or changed')
if native_task is not None:
    import runpy
    script = Path(root) / 'tasks' / native_task / 'experiment.py'
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name='__main__')
else:
    from sapi_config_lab.coordinate.evaluation import main
    raise SystemExit(main(arguments))
"""


def _relative(name: str) -> Path:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name:
        raise ValueError("Unsafe archived source path: " + name)
    return Path(name)


def _capture(root: Path, manifest: dict) -> dict[str, bytes]:
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Archived source manifest must contain frozen file hashes")
    captured = {}
    for name, expected in manifest.items():
        relative = _relative(name)
        path = root / relative
        if any((root / parent).is_symlink() for parent in (relative, *relative.parents)) or not path.is_file():
            raise ValueError("Archived source snapshot unavailable: " + name)
        content = path.read_bytes()
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid archived source hash: " + name)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("Archived source snapshot differs: " + name)
        if path.suffix in {".pyc", ".pyo"} or "__pycache__" in relative.parts:
            raise ValueError("Archived source manifest must not contain bytecode")
        captured[name] = content
    return captured


def _record_identity(metadata: dict, captured: dict[str, bytes]) -> None:
    identity = metadata["identity"]
    options = json.dumps(metadata["options"], sort_keys=True, separators=(",", ":"), allow_nan=False)
    if identity["options_json"] != options or identity["benchmark_id"] != metadata["name"]:
        raise ValueError("Recorded benchmark options identity differs")
    payload = [identity["version"], identity["benchmark_id"], identity["files"], options]
    if hashlib.sha256(json.dumps(payload).encode()).hexdigest() != identity["sha256"]:
        raise ValueError("Recorded benchmark identity digest differs")
    matches = [
        (Path(name).parent, json.loads(content))
        for name, content in captured.items()
        if Path(name).name == "scenario.json" and json.loads(content).get("id") == metadata["name"]
    ]
    if len(matches) != 1:
        raise ValueError("Recorded benchmark is unavailable in archived snapshot")
    directory, declaration = matches[0]
    if declaration["version"] != identity["version"]:
        raise ValueError("Recorded benchmark version differs")
    declared = {"scenario.json", declaration["reference"], *declaration["public"], *declaration["trusted"]}
    for alias, dependency in declaration["dependencies"].items():
        declared.update(
            "dependencies/" + alias + "/" + name for name in (*dependency["public"], *dependency["trusted"])
        )
    if sorted(declared) != [name for name, _ in identity["files"]]:
        raise ValueError("Recorded benchmark source closure differs")
    for name, expected in identity["files"]:
        relative = _relative(name)
        source = directory / relative
        if relative.parts[0] == "dependencies":
            _, alias, *parts = relative.parts
            source = directory.parent / _relative(declaration["dependencies"][alias]["path"]) / Path(*parts)
        if source.as_posix() not in captured or hashlib.sha256(captured[source.as_posix()]).hexdigest() != expected:
            raise ValueError("Recorded benchmark source identity differs: " + name)
    if not metadata["core_files"]:
        raise ValueError("Recorded trusted core source identity is missing")
    for name, expected in metadata["core_files"].items():
        relative = _relative(name)
        source = Path("src") / relative if relative.parts[0] == "sapi_config_lab" else relative
        if source.as_posix() not in captured or hashlib.sha256(captured[source.as_posix()]).hexdigest() != expected:
            raise ValueError("Recorded trusted core source identity differs: " + name)
    if set(metadata.get("runtime_options", {})) - {"mode", "selected_case", "deadline_seconds"}:
        raise ValueError("Unsupported archived runtime options")


def reevaluate_versioned(
    record: Path,
    output: Path,
    source_root: Path | None,
    source_manifest: Path | None,
    *,
    judgement: Path | None = None,
    dispatch: bool = False,
    calibration: str | None = None,
    judge_model: str | None = None,
    series_dir: Path | None = None,
    series_ceiling: list[str] | None = None,
) -> dict:
    """Run the archived guarded evaluator; its ledger alone reserves authorized calls."""
    if source_root is None or source_manifest is None:
        raise ValueError(
            "Versioned re-evaluation requires --source-root and --source-manifest for the archived checkout"
        )
    record, output, source_root = record.resolve(), output.resolve(), source_root.resolve()
    if output.exists():
        raise FileExistsError(output)
    if output.is_relative_to(record / "evidence"):
        raise ValueError("Derived evaluation output must be outside recorded evidence")
    if judgement is not None and dispatch:
        raise ValueError("Supply a saved judgement or dispatch the judge, not both")
    if calibration and not judge_model:
        raise ValueError("Calibration needs a judge model")
    manifest = json.loads(source_manifest.read_text())
    captured = _capture(source_root, manifest)
    metadata = json.loads((record / "benchmark.json").read_text())
    _record_identity(metadata, captured)
    for required in (
        "__init__.py",
        "coordinate/__init__.py",
        "coordinate/evaluation.py",
        "coordinate/provenance.py",
        "coordinate/ledger.py",
    ):
        if "src/sapi_config_lab/" + required not in captured:
            raise ValueError("Archived guarded evaluation source is unavailable: " + required)
    arguments = ["--record", str(record), "--output", str(output)]
    for flag, value in (
        ("--judgement", judgement.resolve() if judgement else None),
        ("--calibration", calibration),
        ("--judge-model", judge_model),
        ("--series-dir", series_dir.resolve() if series_dir else None),
    ):
        if value is not None:
            arguments.extend((flag, str(value)))
    if dispatch:
        arguments.append("--dispatch-judge")
    for ceiling in series_ceiling or []:
        arguments.extend(("--series-ceiling", ceiling))
    return _invoke_snapshot(record, output, source_root, manifest, captured, arguments)


def invoke_native_snapshot(
    record: Path,
    output: Path,
    source_root: Path | None,
    source_manifest: Path | None,
    request: dict,
) -> dict:
    """Replay a native record through its matching archived task-owned evaluator."""
    if source_root is None or source_manifest is None:
        raise ValueError("Native archived evaluation requires --source-root and --source-manifest")
    record, output, source_root = record.resolve(), output.resolve(), source_root.resolve()
    if output.exists():
        raise FileExistsError(output)
    if output.is_relative_to(record / "evidence"):
        raise ValueError("Derived evaluation output must be outside recorded evidence")
    allowed = {
        "action",
        "record",
        "output",
        "judgement",
        "dispatch",
        "calibration",
        "judge_model",
        "series_dir",
        "series_ceiling",
    }
    if not isinstance(request, dict) or set(request) - allowed:
        raise ValueError("Unsupported archived native evaluation request")
    if type(request.get("dispatch", False)) is not bool:
        raise ValueError("Judge dispatch must be boolean")
    if request.get("judgement") and request.get("dispatch"):
        raise ValueError("Supply a saved judgement or dispatch the judge, not both")
    if request.get("calibration") and not request.get("judge_model"):
        raise ValueError("Calibration needs a judge model")
    manifest = json.loads(source_manifest.read_text())
    metadata = json.loads((record / "native-task.json").read_text())
    name = metadata.get("name")
    if (
        metadata.get("schema") != "sapi-lab-native-task/v1"
        or not isinstance(name, str)
        or re.fullmatch(r"[a-z][a-z0-9-]*", name) is None
    ):
        raise ValueError("Unsupported archived native task identity")
    if metadata.get("sources") != manifest:
        raise ValueError("Recorded native source identity differs from supplied manifest")
    captured = _capture(source_root, manifest)
    for required in (
        "src/sapi_config_lab/__init__.py",
        "src/sapi_config_lab/coordinate/__init__.py",
        "src/sapi_config_lab/coordinate/provenance.py",
        f"tasks/{name}/experiment.py",
    ):
        if required not in captured:
            raise ValueError("Archived native evaluation source is unavailable: " + required)
    request = {**request, "action": "evaluate", "record": str(record), "output": str(output)}
    return _invoke_snapshot(record, output, source_root, manifest, captured, [], native_task=name, request=request)


def _invoke_snapshot(
    record: Path,
    output: Path,
    source_root: Path,
    manifest: dict,
    captured: dict[str, bytes],
    arguments: list[str],
    *,
    native_task: str | None = None,
    request: dict | None = None,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="sapi-archived-") as temporary:
        root = Path(temporary).resolve() / "snapshot"
        root.mkdir()
        for name, content in captured.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        # The old checkout scorer owns this cache contract; copy only its pinned bytes.
        contract = record / "evaluation/task-contract.json"
        if contract.is_file():
            source = json.loads(contract.read_text())["source"]
            revision = source["revision"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Invalid archived upstream revision")
            cache = Path(".cache/autowfbench") / revision
            for name, content in _capture(source_root / cache, source["files"]).items():
                target = root / cache / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        frozen = Path(temporary) / "manifest.json"
        frozen.write_text(json.dumps(manifest))
        dependencies = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        environment.update(SAPI_LAB_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                _PROGRAM,
                json.dumps([str(root), str(source_root), str(frozen), dependencies, arguments, native_task]),
            ],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            input=json.dumps(request) if request is not None else None,
            check=False,
        )
        if completed.returncode not in (0, 1) or not (output / "result.json").is_file():
            raise ValueError("Archived evaluation failed: " + completed.stderr.strip())
        return validate_result(json.loads((output / "result.json").read_text()))
