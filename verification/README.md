# Independent verifier

These modules are copied flat into every fixture task's `tests/` and judge a submission without importing the compiler, operations, runtime or coordinator. Duplicating business rules here is deliberate: reusing the implementation under test would make a shared bug look like correctness.

## Flow

1. `verify.py plan` writes every definition the trusted step must run: the submission with each fixture's inputs, negative inputs, a corrupted-artifact probe and deliberately invalid definitions.
2. `sapi_config_lab.coordinate.observe` runs exactly that plan in real n8n and records evidence plus `observation.json`.
3. `verify.py evaluate` checks the record is complete, unaltered and of exactly that plan, then judges it. Decisions go to `evaluation/` (`report.json`, per-case `acceptance.json`, optional `evaluation.json`); `evidence/` is never modified.

`tests/test.sh` writes reward `1` only when evaluation passes. Oracle copies the reference config; nop submits nothing and must score zero.

## Modules

| Module | Role |
| --- | --- |
| `verify.py` | plan, evidence integrity, case judging, CLI |
| `business.py` | invoice-total, ticket-routing, competitor-report obligations |
| `scenario_contracts.py`, `roles.py` | role contracts; bind any unambiguous submitted step IDs to them |
| `scenario_business.py` | obligations of the composed scenarios (06-09) |
| `extensions.py` | bounded refinement (revise-answer) |
| `lifecycle.py`, `lifecycle_submission.py` | daily-digest lifecycle snapshots |
| `n8n_provenance.py` | ties observations to native n8n records |
| `rubric.py`, `rubric_cards.py`, `rubric_facts.py` | optional quality score beside acceptance |

## Rules

- Acceptance needs both business obligations and native n8n provenance.
- Expected answers are recomputed from fixture inputs; the source fixtures are visible test inputs, not a held-out benchmark.
- A named rubric check calls an existing obligation, so it cannot drift from acceptance. The rubric reads the verdict and never sets it; `reward.txt` comes from acceptance alone.
- A card with judged criteria has no judge in the container: it records `not_evaluated` with a reason and a null score.
- `check_runtime_sources` detects edits to the packaged runtime inside the container; it is not a sandbox.
- Live mode (`SAPI_LLM_MODE=live`, `SAPI_CASE_NAME`, `SAPI_BRIDGE_URL`) runs one declared live case per grant with a 600-second deadline.
