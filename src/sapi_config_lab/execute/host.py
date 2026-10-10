"""The machine an experiment runs on: its configuration and the host tools it drives (Harbor, Docker)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, fields
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from sapi_config_lab.execute.n8n import PINNED_N8N_VERSION
from sapi_config_lab.paths import display_path, workspace_root

HARBOR_VERSION = "0.21.0"
LAB_IMAGE = f"sapi-config-lab-n8n:{PINNED_N8N_VERSION}"
HEARTBEAT_SECONDS = 15
ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class LoggedEvent:
    """One `run_logged` fact: started, heartbeat, exited or timed_out; `line` is its standalone stderr text."""

    kind: str
    stage: str
    log: Path
    elapsed: float
    line: str
    last: str | None = None
    quiet: int | None = None
    code: int | None = None


# A command's single progress display observes logged subprocesses here instead of competing on stderr.
LOGGED_OBSERVER: ContextVar[Callable[[LoggedEvent], None] | None] = ContextVar("logged_observer", default=None)


def duration(seconds: float) -> str:
    """Elapsed time for people: `<1s`, `7s` or `1m52s`; recorded timings keep their own precision."""
    if seconds < 1:
        return "<1s"
    whole = int(seconds)
    return f"{whole}s" if whole < 60 else f"{whole // 60}m{whole % 60:02d}s"


def report_logged(event: LoggedEvent) -> None:
    """Hand the event to the installed display, or print the standalone line."""
    observer = LOGGED_OBSERVER.get()
    if observer is None:
        print(event.line, file=sys.stderr, flush=True)
    else:
        observer(event)


@dataclass(frozen=True)
class HostConfig:
    """Machine-specific settings. Defaults describe one Docker Desktop host; each has a SAPI_* override."""

    wrapper_url: str = "http://127.0.0.1:8765/run"  # SAPI_WRAPPER_URL: the local model wrapper
    wrapper_model: str = "gpt-6-astra"  # SAPI_WRAPPER_MODEL: the model the wrapper must report
    container_host: str = "host.docker.internal"  # SAPI_CONTAINER_HOST: how a container reaches the host
    listen_host: str = "127.0.0.1"  # SAPI_LISTEN_HOST: where host services for containers listen
    bridge_port: int = 18765  # SAPI_BRIDGE_PORT: Agency bridge for live experiments

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


def log_tail(log: Path | None, *, stderr: str | bytes | None = None, lines: int = 40, width: int = 300) -> list[str]:
    """Read bounded, normalized log lines for progress and failure diagnostics."""
    if log is not None:
        try:
            with log.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 8192))
                tail = stream.read(8192)
        except OSError:
            return []
    else:
        tail = stderr.encode("utf-8", errors="replace") if isinstance(stderr, str) else stderr or b""
        tail = tail[-8192:]
    segments = re.split(r"[\r\n]", ANSI_CSI.sub("", tail.decode("utf-8", errors="replace")))
    result: list[str] = []
    size = 0
    for segment in reversed(segments):
        line = segment.strip()
        if not line:
            continue
        if len(line) > width:
            line = line[: width - 1] + "…"
        size += len(line.encode("utf-8")) + bool(result)
        if size > 8192:
            break
        result.append(line)
        if len(result) == lines:
            break
    return list(reversed(result))


def last_log_line(log: Path) -> str:
    """Read one bounded, printable progress line from a subprocess log."""
    lines = log_tail(log, lines=1, width=160)
    return lines[0] if lines else "(no output yet)"


def run_logged(
    command: Sequence[str], log: Path, *, stage: str, timeout: float | None, heartbeat: float = HEARTBEAT_SECONDS
) -> int:
    """Run from the checkout with logged output and stderr progress; return the exit code."""
    argv = list(command)
    start = time.monotonic()
    previous_size = 0
    last_growth = start
    log_path = log.resolve()
    report_logged(LoggedEvent("started", stage, log_path, 0, f"[{stage}] started · log {display_path(log_path)}"))
    with log.open("w") as stream:
        process = subprocess.Popen(argv, cwd=workspace_root(), stdout=stream, stderr=subprocess.STDOUT)
        try:
            next_heartbeat = start + heartbeat
            while True:
                now = time.monotonic()
                deadline = start + timeout if timeout is not None else float("inf")
                wait_for = max(0, min(next_heartbeat, deadline) - now)
                try:
                    code = process.wait(timeout=wait_for)
                except subprocess.TimeoutExpired:
                    now = time.monotonic()
                    if now >= deadline:
                        assert timeout is not None
                        line = f"[{stage}] timed out after {duration(now - start)}; outcome unknown"
                        report_logged(LoggedEvent("timed_out", stage, log_path, now - start, line))
                        raise subprocess.TimeoutExpired(argv, timeout) from None
                    try:
                        size = log.stat().st_size
                    except OSError:
                        size = 0
                    if size > previous_size:
                        last_growth = now
                    previous_size = size
                    quiet = int(now - last_growth) if size == 0 or now > last_growth else None
                    last = last_log_line(log)
                    line = f"[{stage}] {duration(now - start)} · log {display_path(log_path)} · last: {last}"
                    line += f" · quiet {duration(quiet)}" if quiet is not None else ""
                    report_logged(LoggedEvent("heartbeat", stage, log_path, now - start, line, last, quiet))
                    next_heartbeat += heartbeat
                    continue
                elapsed = int(time.monotonic() - start)
                line = f"[{stage}] exit {code} after {duration(elapsed)}"
                report_logged(LoggedEvent("exited", stage, log_path, elapsed, line, code=code))
                return code
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def docker_preflight(timeout: float = 20) -> dict[str, str]:
    """Check Docker readiness before dispatching any experiment work."""
    context = "unknown"
    try:
        context = (
            subprocess.check_output(
                ["docker", "context", "show"], text=True, stderr=subprocess.PIPE, timeout=timeout
            ).strip()
            or context
        )
    except FileNotFoundError as error:
        raise RuntimeError("docker CLI not found on PATH") from error
    except subprocess.CalledProcessError, subprocess.TimeoutExpired:
        pass
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RuntimeError("docker CLI not found on PATH") from error
    except subprocess.CalledProcessError as error:
        cause = error.stderr.splitlines()[0] if error.stderr else str(error)
        raise RuntimeError(f"Docker daemon not reachable (context {context}): {cause}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Docker daemon did not answer within {timeout:g}s (context {context})") from error
    return {"server_version": result.stdout.strip(), "context": context}


def image_id(image: str) -> str:
    """The content identity behind a tag. Evidence pins this, never the tag."""
    try:
        return subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True, stderr=subprocess.PIPE
        ).strip()
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stderr or "").lower()
        if any(f"no such {kind}: {image.lower()}" in diagnostic for kind in ("image", "object")):
            raise RuntimeError(f"Docker image {image} is missing; rebuild without --skip-build") from error
        sys.stderr.write(error.stderr or "")
        raise


def pin_base_image(identity: str, prefix: str) -> str:
    """Retag a verified image id under a run-specific name, since Dockerfile FROM cannot take a raw local id."""
    tag = f"{prefix}-base:" + identity.split(":")[-1][:16]
    subprocess.check_call(["docker", "tag", identity, tag])
    return tag


def running_containers() -> str:
    """Metadata only: never inspect an environment or read an existing volume."""
    return subprocess.check_output(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Image}}"], text=True)
