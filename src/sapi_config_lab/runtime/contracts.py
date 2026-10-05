"""The supported workflow backend interface, independent of an execution engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypedDict

Document = dict[str, Any]
LlmMode = Literal["stub", "live"]
ArtifactTransform = Callable[[Document], Document]


@dataclass(frozen=True)
class CompileOptions:
    llm_mode: LlmMode = "stub"
    bridge_url: str | None = None
    request_timeout_seconds: int = 190
    admission: Document | None = None
    deadline_at: float | None = None
    operation_url: str | None = None
    operation_token: str | None = None


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
    """Compile a definition, then execute the resulting artifact in its engine.

    Compilation raises profile.Invalid or profile.Unsupported before engine I/O.
    Execution returns engine evidence and must not equate a process exit code with
    workflow success. Neither method evaluates business acceptance.
    """

    name: str

    def compile(self, config: Document, bindings: Document, options: CompileOptions) -> CompiledWorkflow: ...

    def execute(self, compiled: CompiledWorkflow, artifact_dir: Path) -> ExecutionRecord: ...
