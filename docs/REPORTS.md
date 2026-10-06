# Reading execution and acceptance reports

A workflow can finish successfully and still return the wrong business result.
The runtime and independent verifier record those decisions separately in new
`sapi-lab-execution/v1` case records. Historical reports retain their old schema.

| Field | Meaning |
| --- | --- |
| `execution.status`, `execution.succeeded` | The selected engine imported/executed the workflow and produced its required runtime result; no business acceptance is implied |
| `execution.engine` | Adapter name and observed engine version |
| `acceptance.status`, `acceptance.passed` | Always `not_evaluated` and `null` in `case.json`: the execution record is never rewritten. The verifier's decision is the separate `acceptance.json` beside it (`sapi-lab-acceptance/v1`: `status`, `passed`, `reason`, the sha256 of the `case.json` and `config.json` it judged, and the evaluator). Reports recorded before 2026-10-06 carry the decision inside `case.json` instead |
| `input.source`, `input.activation` | `fixture` and `injected`: test data was supplied directly, even though the execution engine is real |
| `llm.selected_mode` | Configured `stub` or `live` mode; it does not establish that this particular workflow called a model |
| `llm.agency_http_call_count` | Observed Agency HTTP node attempts in native n8n execution data, including failed requests; unavailable evidence can leave this `null` |
| `llm.count_source` | Where the attempt count was observed; the n8n adapter uses `n8n_agency_http_node_executions` |
| `llm.provider_call_count` | `null` when provider calls cannot be established from runtime evidence; an Agency HTTP attempt is not proof of a provider invocation |
| `evidence.engine` | Native evidence kind, source, and artifact references for raw/persisted execution data |
| `evidence.workflow_trace` | Events authored by generated workflow code, explicitly distinguished from native engine records |

The independent verifier checks both business criteria and native execution
provenance before accepting a result. Logical output or a workflow-authored trace
alone cannot establish that required n8n nodes really ran. The current native
provenance checker supports n8n; another execution engine needs its own checker.

Flat fields such as `status`, `n8n_version`, `run_data`, `llm_mode`, and the
workflow's legacy `simulation: true` remain for compatibility. Prefer the v1
sections when interpreting new records. Fixture injection does not mean n8n was
simulated; `simulation` was the original name for that input setup.

## Negative checks and suite success

A verifier case's `acceptance` describes that execution result. A deliberately
incorrect result can have `execution.succeeded: true` and rejected acceptance.
A negative test then passes because rejection was the expected behavior.

The verifier's top-level `passed` and case row `passed` fields describe whether
the test expectations were satisfied, including negative tests. They are not
aliases for every workflow having accepted business output. Harbor reward 1
means the complete task suite passed; nop controls are expected to receive 0.

## The rubric document beside the report

For the three scenarios that have a rubric card, the verifier also writes
`evaluation.json` next to `report.json`, under `sapi-lab-rubric-evaluation/v1`.
It is a weighted quality score out of ten and it is **not** the acceptance
decision: `reward.txt` still comes from the verifier's exit status alone, and
nothing in the reward chain reads this file. A card whose criteria include a
judged question is reported as `not_evaluated` with a null score, because no
judge is reachable from the task container — never as a zero. Scenarios without
a card write no `evaluation.json` at all. The benchmark track writes a different
document under the same filename; tell them apart by `schema`, not by path. See
[`verification/README.md`](../verification/README.md) for the card structure and
[the glossary](GLOSSARY.md#frozen-schemas) for the schema table.

## Local evidence and source identity

New control runs create `reports/<timestamp>/report.json`, a public-source hash
manifest, host and container versions, task packages, and per-case evidence.
These outputs remain local and are excluded from Git and distributions. The
[curated results](history/RESULTS.md) explain what historical series established.

Source fingerprints cover runtime, verifier, prompt/catalog data, lockfiles,
and reproduction code. New modules are discovered automatically. They exclude
local environment snapshots and raw run outputs. Changing public sources during
a control or generation series invalidates its overall status; preserve the
failed attempt and choose a new report directory for a fresh run.

## Generated live replay reports

The additive `sapi-lab-generated-live/v1` experiment report separates the unpaid
preflight, live trial acceptance, native Agency HTTP attempts, outgoing wrapper
attempts and confirmed completions. The correlation table requires actual
native HTTP records plus dispatch and response evidence. Provider-internal counts
remain unknown without receipts. Original submission bytes and each executed
fixture config have separate hashes. Failed evidence collection leaves a failed
report and preserves partial artifacts. See [the command and schema notes](GENERATED-LIVE.md).
