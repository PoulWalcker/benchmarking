# Research Report: multi-LLM authoring benchmark

Submit a valid `sapi-lab/v0` YAML workflow to `/app/submission/config.yaml`.
The Oracle is retained separately and is not supplied as an agent-authored answer.

Build a Gantt with validation, two independent LLM analysis steps, an explicit
`all_terminal` join, and a final LLM report step. Use only registered `research.*`
operations. Include exactly the output fields `report` and `evidence` in final
output; preserve every named fact and only quote evidence from source input.
Inputs will be replaced with other documents; do not hard-code the report.
Reject blank material. No external research, internet tools, arbitrary code, or
changes to compiler or verifier are allowed.

Tests compile submitted YAML, execute a pinned real n8n process in an isolated
Harbor verifier, and check engine provenance plus independently stated acceptance.
For unpaid controls, LLM steps use explicitly deterministic stubs. Live model
quality and semantic judge decisions are **not** demonstrated by the stub score.
