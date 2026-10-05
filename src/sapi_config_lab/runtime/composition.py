"""Select the implemented backend at the application's composition point."""

from sapi_config_lab.core.contracts import WorkflowBackend


def default_backend() -> WorkflowBackend:
    from sapi_config_lab.runtime.n8n.adapter import N8nBackend

    return N8nBackend()
