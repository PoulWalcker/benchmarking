"""The host tools an experiment drives: the pinned Harbor, and Docker.

Harbor is taken from this interpreter, never from a global tool. The Docker
helpers are the read-and-tag operations every experiment repeats; anything that
runs a container stays with the experiment that owns that container.
"""

from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess
import sys


def harbor_command() -> list[str]:
    try:
        installed = version("harbor")
    except PackageNotFoundError as error:
        raise RuntimeError("Run uv sync --extra harbor before experiments") from error
    if installed != "0.21.0":
        raise RuntimeError(f"Expected Harbor 0.21.0, got {installed}")
    return [sys.executable, "-c", "from harbor.cli.main import app; app()"]


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
