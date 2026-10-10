"""One stderr progress display per command, projected from the orchestration events its producers own.

Nothing here infers Harbor, Docker or model phases: a subprocess is shown as running until it
returns, and its heartbeat proves only that the parent still waits. Counters count stages, not work.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import TextIO

from sapi_config_lab.execute.host import HEARTBEAT_SECONDS, LOGGED_OBSERVER, LoggedEvent

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MARKS = {"pending": "·", "done": "✓", "failed": "✗", "unknown": "?", "interrupted": "!"}
ENDED = frozenset({"done", "failed", "unknown", "interrupted"})
OUTCOMES = {"passed": "✓", "failed": "✗", "unknown": "?", "interrupted": "!"}
REFRESH_SECONDS = 0.1


def duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    return f"{whole // 60}m{whole % 60:02d}s"


@dataclass
class Stage:
    name: str
    state: str = "pending"
    started: float | None = None
    ended: float | None = None


def terminal_size(stream: TextIO) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(stream.fileno())
    except AttributeError, OSError, ValueError:
        return 80, 24
    # A pseudo-terminal without a configured size reports 0x0.
    return size.columns or 80, size.lines or 24


class Tracker:
    """Ordered stage state and its renderer: an in-place frame on a terminal, plain lines otherwise."""

    def __init__(
        self,
        command: str,
        stages: Sequence[str],
        *,
        stream: TextIO,
        clock: Callable[[], float] = time.monotonic,
        interactive: bool | None = None,
        heartbeat: float = HEARTBEAT_SECONDS,
        size: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.command = command
        self.stages = [Stage(name) for name in dict.fromkeys(stages)]
        self.stream = stream
        self.clock = clock
        if interactive is None:
            interactive = stream.isatty() and os.environ.get("TERM", "") != "dumb"
        self.interactive = interactive
        self.heartbeat = heartbeat
        self.size = size or (lambda: terminal_size(stream))
        self.started = clock()
        self.details: list[str] = []
        self.child: LoggedEvent | None = None
        self.child_seen = self.started
        self.outcome: str | None = None
        self.height = 0
        self.tick = 0
        self.written = self.started
        self.lock = threading.RLock()
        with self.lock:
            if self.interactive:
                self._draw()
            else:
                self._line(f"started · stages: {' → '.join(stage.name for stage in self.stages)}")

    # Events reported by producers.

    def add_stages(self, names: Sequence[str]) -> None:
        """Insert newly known stages after the latest started one."""
        with self.lock:
            known = {stage.name for stage in self.stages}
            started = [i for i, stage in enumerate(self.stages) if stage.state != "pending"]
            at = started[-1] + 1 if started else 0
            added = [Stage(name) for name in dict.fromkeys(names) if name not in known]
            self.stages[at:at] = added
            self._render()

    def start(self, name: str) -> None:
        with self.lock:
            stage = self._stage(name)
            if stage.state != "pending" or self.outcome is not None:
                return
            stage.state, stage.started = "running", self.clock()
            if self.interactive:
                self._draw()
            else:
                self._line(f"{self._position(stage)} · running")

    def end(self, name: str, state: str) -> None:
        """End a running stage; a pending or already ended stage never changes here."""
        if state not in ENDED:
            raise ValueError(f"Unknown stage state {state}")
        with self.lock:
            stage = next((stage for stage in self.stages if stage.name == name), None)
            if stage is None or stage.state != "running":
                return
            stage.state, stage.ended = state, self.clock()
            self.details.clear()
            self.child = None
            if self.interactive:
                self._draw()
            else:
                assert stage.started is not None
                self._line(f"{self._position(stage)} · {state} {duration(stage.ended - stage.started)}")

    def unknown_outcome(self) -> None:
        """An already ended failure is reclassified as unknown, never as passed."""
        with self.lock:
            for stage in self.stages:
                if stage.state == "failed":
                    stage.state = "unknown"
            self._render()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Run a stage; its exception propagates unchanged after the stage is ended."""
        self.start(name)
        try:
            yield
        except subprocess.TimeoutExpired:
            self.end(name, "unknown")
            raise
        except Exception:
            self.end(name, "failed")
            raise
        except KeyboardInterrupt:
            self.end(name, "interrupted")
            raise
        self.end(name, "done")

    @contextmanager
    def detail(self, text: str) -> Iterator[None]:
        """Nested context for the running stage, such as the dispatched job or reserved calls."""
        with self.lock:
            self.details.append(text)
            if self.interactive:
                self._draw()
            else:
                self._line("  " + text)
        try:
            yield
        finally:
            with self.lock:
                if text in self.details:
                    self.details.remove(text)
                self._render()

    def observe(self, event: LoggedEvent) -> None:
        """A logged subprocess fact from `run_logged`; it is metadata, never a phase transition."""
        with self.lock:
            running = event.kind in ("started", "heartbeat")
            self.child = event if running else None
            self.child_seen = self.clock()
            if not self.interactive:
                self._write(event.line + "\n")
            elif event.kind == "timed_out" or (event.kind == "exited" and event.code):
                self.note(event.line)
            else:
                self._draw()

    def note(self, message: str) -> None:
        """A durable message line above the live frame."""
        with self.lock:
            if self.interactive:
                self._draw(before=message)
            else:
                self._write(message + "\n")

    def refresh(self) -> None:
        """Advance the spinner, or print a bounded heartbeat when no subprocess reports one."""
        with self.lock:
            if self.outcome is not None:
                return
            if self.interactive:
                self.tick += 1
                self._draw()
                return
            running = [stage for stage in self.stages if stage.state == "running"]
            now = self.clock()
            if running and self.child is None and now - self.written >= self.heartbeat:
                stage = running[-1]
                assert stage.started is not None
                self._line(f"{self._position(stage)} · running {duration(now - stage.started)}")

    def finish(self, outcome: str) -> None:
        """Render the final status exactly once; a still running stage is never shown as done."""
        if outcome not in OUTCOMES:
            raise ValueError(f"Unknown outcome {outcome}")
        with self.lock:
            if self.outcome is not None:
                return
            now = self.clock()
            for stage in self.stages:
                if stage.state == "running":
                    stage.state = "interrupted" if outcome == "interrupted" else "unknown"
                    stage.ended = now
            self.outcome = outcome
            self.details.clear()
            self.child = None
            if self.interactive:
                self._draw()
            else:
                self._line(self._summary(now))

    # Rendering.

    def _stage(self, name: str) -> Stage:
        for stage in self.stages:
            if stage.name == name:
                return stage
        self.add_stages([name])
        return self._stage(name)

    def _position(self, stage: Stage) -> str:
        return f"{self.stages.index(stage) + 1}/{len(self.stages)} {stage.name}"

    def _summary(self, now: float) -> str:
        done = sum(stage.state == "done" for stage in self.stages)
        text = (
            f"{'outcome unknown' if self.outcome == 'unknown' else self.outcome} after {duration(now - self.started)}"
        )
        text += f" · {done}/{len(self.stages)} stages done"
        for state in ("failed", "unknown", "interrupted"):
            names = [stage.name for stage in self.stages if stage.state == state]
            if names:
                text += f" · {state}: {', '.join(names)}"
        if self.outcome == "failed" and not any(stage.state in ENDED - {"done"} for stage in self.stages):
            text += " · result not accepted"
        pending = [stage.name for stage in self.stages if stage.state == "pending"]
        if pending:
            text += f" · not run: {', '.join(pending)}"
        return text

    def _line(self, text: str) -> None:
        self._write(f"[{self.command}] {text}\n")

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
            self.stream.flush()
        except OSError, ValueError:
            return
        self.written = self.clock()

    def _render(self) -> None:
        if self.interactive:
            self._draw()

    def _frame(self) -> list[str]:
        now = self.clock()
        spin = SPINNER[self.tick % len(SPINNER)]
        running = [stage for stage in self.stages if stage.state == "running"]
        current = running[-1] if running else None
        if self.outcome is None:
            head = f"{spin} {self.command} · {duration(now - self.started)}"
            if current is not None:
                head += f" · stage {self._position(current)}"
        else:
            head = f"{OUTCOMES[self.outcome]} {self.command} {self._summary(now)}"
        width = max(len(stage.name) for stage in self.stages) if self.stages else 0
        rows: list[tuple[Stage, list[str]]] = []
        for stage in self.stages:
            if stage.state == "running":
                assert stage.started is not None
                lines = [f"  {spin} {stage.name:<{width}}  {duration(now - stage.started)}"]
            elif stage.state in ENDED and stage.started is not None and stage.ended is not None:
                lines = [f"  {MARKS[stage.state]} {stage.name:<{width}}  {duration(stage.ended - stage.started)}"]
            else:
                lines = [f"  {MARKS[stage.state]} {stage.name}"]
            if stage is current and self.outcome is None:
                lines += [f"      {text}" for text in self.details]
                if self.child is not None:
                    child = self.child
                    elapsed = duration(child.elapsed + now - self.child_seen)
                    status = f"      {child.stage} {elapsed}"
                    if child.last is not None:
                        status += f" · last: {child.last}"
                    if child.quiet is not None:
                        status += f" · quiet {child.quiet}s"
                    lines += [status, f"      log {child.log}"]
            rows.append((stage, lines))
        columns, height = self.size()
        body = self._compact(rows, max(4, height - 2))
        return [line if len(line) < columns else line[: max(1, columns - 2)] + "…" for line in [head, *body]]

    @staticmethod
    def _compact(rows: list[tuple[Stage, list[str]]], limit: int) -> list[str]:
        """Collapse leading done and trailing pending stages so the frame fits the terminal."""
        size = sum(len(lines) for _, lines in rows)
        first, last = 0, len(rows)
        # Each collapsed run costs one summary line of its own.
        while size + bool(first) > limit and first < len(rows) - 1 and rows[first][0].state == "done":
            size -= len(rows[first][1])
            first += 1
        size += bool(first)
        while size + (last < len(rows)) > limit and last - 1 > first and rows[last - 1][0].state == "pending":
            last -= 1
            size -= 1
        body = [f"  ✓ {first} earlier stages done"] if first else []
        body += [line for _, lines in rows[first:last] for line in lines]
        if last < len(rows):
            body.append(f"  · {len(rows) - last} more stages pending")
        return body

    def _draw(self, before: str | None = None) -> None:
        # Return to the frame's first line, clear to the end of the screen and redraw in one write.
        text = f"\x1b[{self.height}A\r" if self.height else ""
        text += "\x1b[J"
        if before is not None:
            text += before + "\n"
        lines = self._frame()
        text += "".join(line + "\n" for line in lines)
        self.height = len(lines)
        self._write(text)


ACTIVE: ContextVar[Tracker | None] = ContextVar("progress_tracker", default=None)


def _refresh(tracker: Tracker, stop: threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        tracker.refresh()


@contextmanager
def tracking(
    command: str,
    stages: Sequence[str],
    *,
    stream: TextIO | None = None,
    refresh: float | None = REFRESH_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    interactive: bool | None = None,
) -> Iterator[Tracker]:
    """Own the terminal's one display; a nested call reuses the outer display instead of competing."""
    active = ACTIVE.get()
    if active is not None:
        yield active
        return
    tracker = Tracker(command, stages, stream=stream or sys.stderr, clock=clock, interactive=interactive)
    token, observer = ACTIVE.set(tracker), LOGGED_OBSERVER.set(tracker.observe)
    stop = threading.Event()
    thread = None
    if refresh is not None:
        interval = refresh if tracker.interactive else 1.0
        thread = threading.Thread(target=_refresh, args=(tracker, stop, interval), name="progress", daemon=True)
        thread.start()
    outcome = "unknown"
    try:
        yield tracker
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        outcome = "failed"
        raise
    except KeyboardInterrupt:
        outcome = "interrupted"
        raise
    finally:
        stop.set()
        if thread is not None:
            thread.join()
        LOGGED_OBSERVER.reset(observer)
        ACTIVE.reset(token)
        tracker.finish(outcome)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """A stage of the active display; without one the block simply runs."""
    tracker = ACTIVE.get()
    if tracker is None:
        yield
        return
    with tracker.stage(name):
        yield


def add_stages(names: Sequence[str]) -> None:
    """Stages that become known during a run, such as admitted cases."""
    tracker = ACTIVE.get()
    if tracker is not None:
        tracker.add_stages(names)


@contextmanager
def detail(text: str) -> Iterator[None]:
    tracker = ACTIVE.get()
    if tracker is None:
        yield
        return
    with tracker.detail(text):
        yield


def note(message: str) -> None:
    """A durable human line on stderr, above any live frame."""
    tracker = ACTIVE.get()
    if tracker is None:
        print(message, file=sys.stderr, flush=True)
    else:
        tracker.note(message)


def failure_lines(log: Path | None, tail: Sequence[str], *, lines: int = 20, jobs: Path | None = None) -> list[str]:
    """The log, job directory, bounded last lines and inspection command for a failed stage."""
    result = [f"  log: {log}"] if log is not None else []
    if jobs is not None:
        result.append(f"  jobs: {jobs}")
    if tail:
        result += ["  last lines:", *(f"    {line}" for line in tail[-lines:])]
    if log is not None:
        result.append(f"  inspect: tail -n 200 {log}")
    return result
