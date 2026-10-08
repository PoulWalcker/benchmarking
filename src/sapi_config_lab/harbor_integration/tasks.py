"""Positively materialize a versioned benchmark as a separate-verifier Harbor task."""

from collections.abc import Mapping
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import tempfile
from typing import Any

from harbor.models.task.config import TaskConfig
from harbor.models.task.task import Task

from sapi_config_lab.benchmark import Benchmark, python_module
from sapi_config_lab.benchmark_loading import BenchmarkIdentity, freeze_identity

RESOURCES = Path(__file__).parent
ARTIFACTS = [
    {"source": "/logs/artifacts", "destination": "discarded-convention", "exclude": ["*"]},
    {"source": "/submission/config.yaml", "destination": "submission/config.yaml"},
]
COLLECT = [{"command": "python3 /opt/sapi-submission.py collect", "user": "root", "timeout_sec": 10.0}]


def validate_config(text: str) -> TaskConfig:
    """Validate native Harbor settings and the supported Docker YAML transfer profile."""
    if version("harbor") != "0.21.0":
        raise ValueError("Task packaging requires Harbor 0.21.0")
    config = TaskConfig.model_validate_toml(text)
    if config.steps or config.environment.os != "linux":
        raise ValueError("This packaging profile requires single-step Linux tasks")
    if config.verifier.environment is None or config.verifier.environment_mode == "shared":
        raise ValueError("An explicit separate verifier environment is required")
    for environment in (config.environment, config.verifier.environment):
        if environment.os != "linux":
            raise ValueError("Both environments must use Linux")
        if environment.docker_image or environment.env or environment.mcp_servers or environment.skills_dir:
            raise ValueError("Prebuilt images, environment injection and external agent inputs are not supported")
    if config.agent.user != "1000":
        raise ValueError("Author must run as unprivileged user 1000")
    if config.verifier.user not in (None, "root", "0", 0):
        raise ValueError("Verifier must be able to read the protected submission")
    if config.solution.env or config.verifier.env:
        raise ValueError("Credential injection is outside this packaging profile")
    actual = [
        item.model_dump(exclude_defaults=True) if not isinstance(item, str) else item for item in config.artifacts
    ]
    if actual != ARTIFACTS:
        raise ValueError("Declare only the protected YAML and excluded conventional artifact directory")
    hooks = [item.model_dump(exclude_defaults=True) for item in config.verifier.collect]
    if hooks != COLLECT:
        raise ValueError("Declare the root-owned YAML collection hook")
    return config


def stage_benchmark(
    benchmark: Benchmark,
    destination: Path,
    options: Mapping[str, Any],
    *,
    core_files: Mapping[str, Path] | None = None,
    instruction: bytes | None = None,
    submission: bytes | None = None,
    oracle: bool = True,
    host_records: Mapping[str, bytes] | None = None,
) -> BenchmarkIdentity:
    """Stage disjoint contexts atomically; no existing task or source bytes are overwritten."""
    identity = freeze_identity(benchmark, options)
    files = {item.destination: item for item in benchmark.files}
    if "instruction.md" not in files or files["instruction.md"].visibility != "public":
        raise ValueError("Declare public instruction.md")
    if "verifier.sh" not in files or files["verifier.sh"].visibility != "trusted":
        raise ValueError("Declare trusted verifier.sh as the verifier entrypoint")
    if files[benchmark.harbor_task].visibility != "trusted":
        raise ValueError("Declare native task configuration as trusted")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Task destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = dict(identity.files)

    def content(name: str) -> bytes:
        data = files[name].source.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError(f"Benchmark source changed during staging: {name}")
        return data

    config_bytes = content(benchmark.harbor_task)
    validate_config(config_bytes.decode())

    with tempfile.TemporaryDirectory(prefix=".sapi-task-", dir=destination.parent) as temporary:
        task = Path(temporary) / benchmark.name
        task.mkdir()

        def write(relative: str, data: bytes) -> None:
            path = task / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)

        write("instruction.md", content("instruction.md") if instruction is None else instruction)
        write("task.toml", config_bytes)
        for item in benchmark.public:
            write("environment/payload/" + item.destination, content(item.destination))
        for item in (*benchmark.public, *benchmark.trusted):
            write("tests/payload/" + item.destination, content(item.destination))
        if oracle:
            write(
                "solution/config.yaml", content(benchmark.reference.destination) if submission is None else submission
            )
            write(
                "solution/solve.sh",
                b"#!/bin/sh\nset -eu\ncp /solution/config.yaml /app/submission/config.yaml\nchmod 644 /app/submission/config.yaml\n",
            )
        core = {}
        for relative, source in (core_files or {}).items():
            parts = relative.split("/")
            if (
                not parts
                or parts[0] not in {"sapi_config_lab", "verification"}
                or any(part in {"", ".", ".."} for part in parts)
            ):
                raise ValueError("Invalid trusted core destination: " + relative)
            if source.is_symlink() or not source.is_file():
                raise ValueError("Trusted core source must be a regular file")
            data = source.read_bytes()
            core[relative] = hashlib.sha256(data).hexdigest()
            write("tests/core/" + relative, data)
        write("tests/core/.keep", b"")
        for name, data in (host_records or {}).items():
            if "/" in name or "\\" in name or not name.endswith(".json") or name == "benchmark.json":
                raise ValueError("Host records must be distinct JSON basenames")
            write("tests/" + name, data)
        write(
            "tests/benchmark.json",
            json.dumps(
                {
                    "name": benchmark.name,
                    "bindings": benchmark.bindings,
                    "operations": benchmark.operations,
                    "entrypoints": {
                        role: {
                            **asdict(entry),
                            "module": "payload" + ("." + module if (module := python_module(entry.path)) else ""),
                        }
                        for role, entry in benchmark.entrypoints.items()
                    },
                    "identity": asdict(identity),
                    "options": json.loads(identity.options_json),
                    "core_files": core,
                    "payload_files": {
                        item.destination: expected[item.destination] for item in (*benchmark.public, *benchmark.trusted)
                    },
                },
                indent=2,
                sort_keys=True,
            ).encode(),
        )
        runtime = (RESOURCES / "runtime/Dockerfile").read_bytes()
        admission = (RESOURCES / "submission.py").read_bytes()
        for area in ("environment", "tests"):
            write(area + "/submission.py", admission)
        write(
            "environment/Dockerfile",
            runtime + b"\nCOPY payload /app/public/\nCOPY submission.py /opt/sapi-submission.py\n"
            b"RUN adduser -D -u 1000 author && mkdir -p /app/submission /submission && "
            b"chown author /app/submission && chmod 700 /submission\nUSER 1000\n",
        )
        write(
            "tests/Dockerfile",
            runtime + b"\nCOPY payload /tests/payload/\nCOPY submission.py /opt/sapi-submission.py\n"
            b"COPY test.sh /tests/test.sh\nCOPY check_imports.py /tests/check_imports.py\n"
            b"COPY core /tests/core/\nCOPY benchmark.json /tests/benchmark.json\nENV PYTHONPATH=/tests/core:/tests\n"
            b"RUN mkdir -p /submission /logs/artifacts && python3 /tests/check_imports.py\n",
        )
        modules = sorted(
            {
                "payload" + ("." + relative if (relative := python_module(entry.path)) else "")
                for entry in benchmark.entrypoints.values()
            }
        )
        write(
            "tests/check_imports.py",
            (
                "import importlib\n" + "\n".join(f"importlib.import_module({module!r})" for module in modules) + "\n"
            ).encode(),
        )
        write(
            "tests/test.sh",
            b"#!/bin/sh\nset -eu\npython3 /opt/sapi-submission.py verify\ncd /tests/payload\nexec bash verifier.sh\n",
        )
        if freeze_identity(benchmark, options) != identity:
            raise ValueError("Benchmark changed during staging")
        Task(task)
        snapshot = {
            "harbor_version": "0.21.0",
            "benchmark": asdict(identity),
            "files": {
                path.relative_to(task).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(task.rglob("*"))
                if path.is_file()
            },
        }
        write("inputs.json", json.dumps(snapshot, indent=2, sort_keys=True).encode())
        task.rename(destination)
    return identity
