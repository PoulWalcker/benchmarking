"""The n8n backend: the compile and execute stages behind one WorkflowBackend."""

from pathlib import Path

from sapi_config_lab.compile.n8n import compile_n8n
from sapi_config_lab.contracts import (
    CompiledWorkflow,
    CompileOptions,
    Document,
    ExecutionRecord,
    RunBinding,
    WorkflowBackend,
)
from sapi_config_lab.execute.n8n import execute_compiled


class N8nBackend:
    name = "n8n"

    def __init__(self, operation_source: str | None = None) -> None:
        self.operation_source = operation_source

    def compile(self, config: Document, bindings: Document, options: CompileOptions) -> CompiledWorkflow:
        document, mapping = compile_n8n(
            config,
            bindings,
            llm_mode=options.llm_mode,
            bridge_url=options.bridge_url,
            request_timeout_seconds=options.request_timeout_seconds,
            operation_url=options.operation_url,
            activation=options.activation,
            bound_deadline=options.bound_deadline,
            operation_source=self.operation_source,
        )
        return CompiledWorkflow(self.name, document, mapping, config["execution"]["deadline_seconds"], options)

    def execute(self, compiled: CompiledWorkflow, artifact_dir: Path, binding: RunBinding) -> ExecutionRecord:
        if compiled.engine != self.name:
            raise ValueError(f"Cannot execute a {compiled.engine} artifact with {self.name}")
        return execute_compiled(compiled, artifact_dir, binding)


def default_backend() -> WorkflowBackend:
    """The application's single composition point for the implemented backend."""
    return N8nBackend()
