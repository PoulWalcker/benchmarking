"""Submit one native Harbor job into its authoritative durable output tree."""

from collections.abc import Sequence
from pathlib import Path
import subprocess

from sapi_config_lab.paths import workspace_root


def job_args(
    harbor: Sequence[str],
    tasks: Path,
    jobs: Path,
    job_name: str,
    agent: str,
    *,
    agent_key: str | None = None,
    attempts: str | None = None,
    verifier_env: Sequence[str] = (),
) -> list[str]:
    """Record the exact serial, retry-free invocation used for native jobs."""
    args = [*harbor, "run", "--path", str(tasks), "--agent", agent]
    if agent_key is not None:
        args += ["--ak", agent_key]
    if attempts is not None:
        args += ["--n-attempts", attempts]
    args += ["--n-concurrent", "1", "--max-retries", "0", "--jobs-dir", str(jobs), "--job-name", job_name]
    for value in verifier_env:
        args += ["--verifier-env", value]
    return [*args, "--force-build"]


def run_job(
    harbor: Sequence[str],
    tasks: Path,
    jobs: Path,
    job_name: str,
    agent: str,
    log: Path,
    *,
    agent_key: str | None = None,
    attempts: str | None = None,
    verifier_env: Sequence[str] = (),
) -> int:
    """Dispatch once with native phase limits and no aggregate watchdog or output copy."""
    from sapi_config_lab.harbor_integration.tasks import validate_config

    if Path(job_name).name != job_name or job_name in {"", ".", ".."}:
        raise ValueError("Job name must be a basename")
    if (jobs / job_name).exists():
        raise ValueError("A Harbor job cannot be dispatched twice")
    selected = [tasks] if (tasks / "task.toml").is_file() else sorted(tasks.iterdir())
    if not selected:
        raise ValueError("No Harbor tasks selected")
    for task in selected:
        validate_config((task / "task.toml").read_text())
    args = job_args(
        harbor, tasks, jobs, job_name, agent, agent_key=agent_key, attempts=attempts, verifier_env=verifier_env
    )
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / job_name).mkdir()
    with log.open("w") as stream:
        return subprocess.run(args, cwd=workspace_root(), stdout=stream, stderr=subprocess.STDOUT).returncode
