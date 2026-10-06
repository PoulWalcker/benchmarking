"""The supported workflow backend interface, independent of an execution engine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypedDict

Document = dict[str, Any]
LlmMode = Literal["stub", "live"]
ArtifactTransform = Callable[[Document], Document]


ADMISSION_FIELDS = frozenset({"kind", "rule_id", "event_id", "workflow_ref", "purpose"})


@dataclass(frozen=True)
class CompileOptions:
    """Definition-time choices; the same definition and options give the same artifact."""

    llm_mode: LlmMode = "stub"
    bridge_url: str | None = None
    request_timeout_seconds: int = 190
    operation_url: str | None = None
    activation: Literal["fixture", "event"] = "fixture"
    bound_deadline: bool = False

    def __post_init__(self) -> None:
        if self.activation == "event" and not self.bound_deadline:
            raise ValueError("An admitted event always runs against a bound deadline")


@dataclass(frozen=True)
class RunBinding:
    """What one execution supplies at run time: never compiled into an artifact."""

    deadline_at: float | None = None
    admission: Document | None = None
    operation_token: str | None = None

    def __post_init__(self) -> None:
        if self.deadline_at is not None and not math.isfinite(self.deadline_at):
            raise ValueError("Invalid absolute deadline")
        if self.admission is not None:
            if set(self.admission) != ADMISSION_FIELDS:
                raise ValueError("Invalid admission fields")
            if not isinstance(self.admission["event_id"], str) or not self.admission["event_id"]:
                raise ValueError("Invalid event ID")
        if self.operation_token is not None and not (isinstance(self.operation_token, str) and self.operation_token):
            raise ValueError("Invalid operation token")


@dataclass(frozen=True)
class CompiledWorkflow:
    """Engine-owned document and mapping, with the deadline needed to execute it."""

    engine: str
    document: Document
    mapping: dict[str, str]
    deadline_seconds: int
    options: CompileOptions


class ExecutionRecord(TypedDict, total=False):
    """Stable run fields; engine-specific raw evidence remains explicitly identified."""

    report_schema: str
    status: str
    output: Any
    result: Document
    workflow_id: str | None
    execution_id: str | None
    engine_version: str | None
    error: Document | None
    artifact_dir: str
    duration_seconds: float
    workflow_sha256: str
    mapping: dict[str, str]
    execution: Document
    acceptance: Document
    input: Document
    llm: Document
    evidence: Document
    # Compatibility fields in reports produced by the n8n adapter.
    n8n_version: str | None
    run_data: Document
    import_exit_code: int | None
    execute_exit_code: int | None
    result_node_present: bool
    persisted_status: str | None
    llm_mode: LlmMode
    refinement: Document


class WorkflowBackend(Protocol):
    """Compile a definition, then execute the resulting artifact in its engine."""

    name: str

    def compile(self, config: Document, bindings: Document, options: CompileOptions) -> CompiledWorkflow: ...

    def execute(self, compiled: CompiledWorkflow, artifact_dir: Path, binding: RunBinding) -> ExecutionRecord: ...
