"""The machine an experiment runs on: its configuration and the host tools it drives (Harbor, Docker)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION
from sapi_config_lab.paths import workspace_root

HARBOR_VERSION = "0.21.0"
LAB_IMAGE = f"sapi-config-lab-n8n:{PINNED_N8N_VERSION}"


@dataclass(frozen=True)
class HostConfig:
    """Machine-specific settings. Defaults describe one Docker Desktop host; each has a SAPI_* override."""

    wrapper_url: str = "http://127.0.0.1:8765/run"  # SAPI_WRAPPER_URL: the local model wrapper
    wrapper_model: str = "gpt-6-astra"  # SAPI_WRAPPER_MODEL: the model the wrapper must report
    container_host: str = "host.docker.internal"  # SAPI_CONTAINER_HOST: how a container reaches the host
    listen_host: str = "127.0.0.1"  # SAPI_LISTEN_HOST: where host services for containers listen
    bridge_port: int = 18765  # SAPI_BRIDGE_PORT: Agency bridge for live experiments
    ui_bridge_port: int = 18766  # SAPI_UI_BRIDGE_PORT: Agency bridge for manual UI runs
    n8n_url: str = "http://localhost:5678"  # SAPI_N8N_URL: the local n8n editor
    n8n_container: str = "n8n-n8n-1"  # SAPI_N8N_CONTAINER: the local n8n container
    staging_dir: str = ""  # SAPI_STAGING_DIR: a directory Docker can mount; empty means the system temp

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> HostConfig:
        values: dict[str, Any] = {}
        for field in fields(cls):
            raw = environ.get("SAPI_" + field.name.upper())
            if raw is not None:
                values[field.name] = int(raw) if field.type == "int" else raw
        return cls(**values)

    def container_url(self, port: int) -> str:
        """A host service as a task container addresses it."""
        return f"http://{self.container_host}:{port}"


def local_address(listen_host: str) -> str:
    """Where this host reaches its own service bound to `listen_host`."""
    return "127.0.0.1" if listen_host in ("0.0.0.0", "") else listen_host


def harbor_command() -> list[str]:
    try:
        installed = version("harbor")
    except PackageNotFoundError as error:
        raise RuntimeError("Run uv sync --extra harbor before experiments") from error
    if installed != HARBOR_VERSION:
        raise RuntimeError(f"Expected Harbor {HARBOR_VERSION}, got {installed}")
    return [sys.executable, "-c", "from harbor.cli.main import app; app()"]


def checked_harbor() -> tuple[list[str], str]:
    """The Harbor argv, after the executable itself reports the pinned version."""
    harbor = harbor_command()
    reported = subprocess.check_output([*harbor, "--version"], text=True).strip()
    if reported != HARBOR_VERSION:
        raise RuntimeError(f"Expected Harbor {HARBOR_VERSION}, got {reported}")
    return harbor, reported


def run_logged(command: Sequence[str], log: Path, *, timeout: float) -> int:
    """Run from the checkout with stdout and stderr in one log; return the exit code."""
    with log.open("w") as stream:
        completed = subprocess.run(
            list(command), cwd=workspace_root(), stdout=stream, stderr=subprocess.STDOUT, timeout=timeout
        )
    return completed.returncode


def build_image(tag: str, log: Path) -> None:
    if run_logged(["docker", "build", "-f", "infra/Dockerfile", "-t", tag, "."], log, timeout=1200):
        raise RuntimeError(f"Image build failed; see {log.name}")


def staging_dir(prefix: str, host: HostConfig) -> Path:
    """A fresh directory for task packages and Harbor jobs, outside the checkout."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=host.staging_dir or None))


def collect_jobs(staging: Path, destination: Path) -> None:
    """Copy Harbor's job tree out of staging, including a failed job's partial output."""
    if (staging / "jobs").exists():
        shutil.copytree(staging / "jobs", destination, dirs_exist_ok=True)


def harbor_run_args(
    harbor: Sequence[str],
    tasks: Path | str,
    jobs: Path | str,
    job_name: str,
    agent: str,
    *,
    agent_key: str | None = None,
    attempts: str | None = None,
    verifier_env: Sequence[str] = (),
) -> list[str]:
    """One serial `harbor run` without retries. Reports record this argv, so its order is evidence."""
    args = [*harbor, "run", "--path", str(tasks), "--agent", agent]
    if agent_key is not None:
        args += ["--ak", agent_key]
    if attempts is not None:
        args += ["--n-attempts", attempts]
    args += ["--n-concurrent", "1", "--max-retries", "0", "--jobs-dir", str(jobs), "--job-name", job_name]
    for value in verifier_env:
        args += ["--verifier-env", value]
    return [*args, "--force-build"]


def image_id(image: str) -> str:
    """The content identity behind a tag. Evidence pins this, never the tag."""
    return subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True).strip()


def pin_base_image(identity: str, prefix: str) -> str:
    """Retag a verified image id under a run-specific name, since Dockerfile FROM cannot take a raw local id."""
    tag = f"{prefix}-base:" + identity.split(":")[-1][:16]
    subprocess.check_call(["docker", "tag", identity, tag])
    return tag


def running_containers() -> str:
    """Metadata only: never inspect an environment or read an existing volume."""
    return subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
