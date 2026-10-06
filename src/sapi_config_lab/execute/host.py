"""The host tools an experiment drives: the pinned Harbor, and Docker.

Harbor is taken from this interpreter, never from a global tool. The helpers
here are the operations every experiment repeats; anything that runs a
container stays with the experiment that owns that container.
"""

from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from sapi_config_lab.paths import workspace_root

HARBOR_VERSION = "0.21.0"
# The lab image every experiment builds its task packages from.
LAB_IMAGE = "sapi-config-lab-n8n:2.41.5"


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


def run_logged(command: Sequence[str], log: Path, *, timeout: float, env: Mapping[str, str] | None = None) -> int:
    """Run from the checkout with stdout and stderr in one log; return the exit code."""
    with log.open("w") as stream:
        completed = subprocess.run(
            list(command), cwd=workspace_root(), env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout
        )
    return completed.returncode


def build_image(tag: str, log: Path) -> None:
    """Build the pinned n8n lab image from infra/Dockerfile."""
    if run_logged(["docker", "build", "-f", "infra/Dockerfile", "-t", tag, "."], log, timeout=1200):
        raise RuntimeError(f"Image build failed; see {log.name}")


def staging_dir(prefix: str) -> Path:
    """A fresh directory Docker can mount. macOS Desktop mounts may be blocked for
    the Docker VM, so stage under /private/tmp; never under the user's n8n folders."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/private/tmp" if Path("/private/tmp").exists() else None))


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
    """One `harbor run` invocation, serial and without retries.

    Experiments record this argv verbatim in their reports, so the order below
    is part of the evidence: appending a flag here changes every report.
    `harbor` is passed in rather than looked up, because a caller that checked
    `--version` must dispatch through the same executable it checked.
    """
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
    """Retag an already verified image under a run-specific local name.

    Dockerfile FROM does not reliably accept a raw local image id, so task
    packages build from this tag; it carries the id so the two cannot drift.
    The caller passes the identity it resolved, because the gate that accepted
    an image has to be the one that gets pinned.
    """
    tag = f"{prefix}-base:" + identity.split(":")[-1][:16]
    subprocess.check_call(["docker", "tag", identity, tag])
    return tag


def running_containers() -> str:
    """Metadata only: never inspect an environment or read an existing volume."""
    return subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
