"""Task-owned configuration for the shared frozen-workflow evaluation runner."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TaskDefinition:
    key: str
    challenge_id: str
    catalog: str
    oracle: str
    artifact_field: str | None
    artifact_name: str | None
    authoring_attempts: int
    runtime_model_cap: int
    live_reference: bool
    authoring_notes: str

    def as_dict(self):
        return {
            "schema": "sapi-lab-task-definition/v1",
            **asdict(self),
            "result": {"final_answer": "string", "artifact_field": self.artifact_field},
            "inputs_actions_constraints_criteria": "original frozen AutoWFBench package; no locally substituted rubric",
        }


TASKS = {
    "checkout": TaskDefinition(
        "checkout",
        "production-checkout-recovery",
        "generation/checkout-bindings.yaml",
        "tests/support/checkout_oracle.py",
        "incident_summary",
        "incident-summary.md",
        2,
        4,
        True,
        "Do not hardcode source patches or incident conclusions before reading runtime evidence.",
    ),
    "crm": TaskDefinition(
        "crm",
        "crm-lead-qualification",
        "generation/crm-bindings.yaml",
        "tests/support/crm_oracle.py",
        None,
        None,
        1,
        1,
        False,
        "Authoring contract v2 (compact): produce concise YAML, using flow-style mappings where helpful. "
        "Choose the simplest valid static DAG; avoid comments, unused inputs and redundant defensive branches. "
        "Read current inquiry, customer clarification, documents, research and CRM evidence before deciding business actions. "
        "Use original tool actions for all changes and verify final state. crm.update has an explicit maximum of two "
        "attempts, retrying only original retryable errors; other tools have one attempt. "
        "A single runtime LLM operation may propose structured actions and a clearly labelled plan narrative. "
        "Use generic report.evidence to attach actual action receipts/readback afterward; never assert an unobserved success. "
        "Tool JSON can be decoded with json.parse when dynamic references or guards are needed. "
        "Preserve protected fields and do not invent customer facts, commitments, or delivery guarantees.",
    ),
}


def task_definition(key: str) -> TaskDefinition:
    for task in TASKS.values():
        if key in (task.key, task.challenge_id):
            return task
    raise ValueError("Unknown benchmark task")
