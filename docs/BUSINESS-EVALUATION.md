# Two-task business evaluation pilot

The common task configuration supports the pinned original Checkout Recovery and
CRM Lead Qualification simulators. `TaskDefinition` selects the public instruction,
operation catalog, original environment, original scorecard, result/artifact
contract, evaluator and trusted oracle. The compiler has no business-case branch.
The original task package freezes inputs, actions, limits and weighted criteria.

After the setup in [Checkout evaluation](CHECKOUT-EVALUATION.md), run:

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab benchmark --task crm --mode live --report-dir reports/crm-new-run
```

The command builds the real n8n image and requires fresh source/image-matched
unpaid reference/nop controls before dispatching models. CRM admits one authoring,
one runtime planning call and one independent original judge call, with no model
retry or YAML repair. The original 120-second runtime and 40-tool budget remain.
`--seed` selects original seeded inputs; it does not create a new task.
The earlier `sapi-lab checkout` command remains supported with its original caps.

CRM's generated graph can gather facts, clarify with the synthetic customer,
consult policies, propose structured actions, update the existing lead, create a
follow-up, send the synthetic response, and read back actual state. A generic
`json.parse` operation supports field references; `report.evidence` appends exact
operation receipts to an explicitly labelled narrative. Neither operation grades
success or substitutes a model's claim for state. The one-call runtime is an
explicit economical configuration, not a promise of optimal prose quality.

Original independent verifiers inspect actual final state, preservation of
protected identity, authorized recipients, exactly one correct follow-up,
customer contact, and recovery/readback after the injected update failure.
Additional seeds and negative controls use those unchanged verifiers. The catalog
limits accepted argument fields/types locally; this is an explicit adapter
contract, not a change to upstream scoring. Four points remain independently
judged semantic quality; deterministic state is worth six points.

External-action transport uses run-scoped operation IDs derived from native
execution identity. A known receipt replay returns the same result without
another mutation; a conflicting key is rejected. Only `crm.update` opts into two
attempts, and only an explicit original retryable error permits the second.
A lost or ambiguous upstream response latches the session instead of blind retry.
Create/send have no automatic retry. Receipt storage lasts for the live session;
this does not claim durable, cross-process exactly-once delivery. Transport audit
is separate from unchanged original business events and the official rubric.

The calibration module freezes supported, contentless and misleading narratives
and expected semantic bands before any judgement. Replacing prose is explicitly
a counterfactual calibration control, not a second solver result. Missing or
simulated judgements never count as measured calibration success. Good historical
judgements retain their own model, prompt, contract and run-log provenance.

Run a single explicit calibration against preserved failed environment evidence:

```bash
uv run --locked --extra benchmark sapi-lab benchmark-calibrate --source .cache/autowfbench/970bbc8645c4d503d35cb5df05363fb9de132519 --base-run reports/two-task-validation-20261005/calibration-base.json --case misleading-success --judge-model gpt-6-astra --output reports/calibration-new-run
```

`--prepare-only` freezes the fixture without dispatch. The live command invokes
one original independent judge with no retry and records measured agreement or
failure. Its result is separate from the official candidate score.

Every result retains criterion → answer → weighted points → cited evidence,
separate execution_pass, original score and Harbor score/10. Failed infrastructure,
invalid/missing judgements and missing artifacts remain unscored, not zero. Stage
summaries count all dispatched authoring/runtime/judge attempts, including failures
and unknown outcomes, with observed time/tokens and unknown monetary/provider cost.
Reference controls, selected candidate results and calibration are never pooled.

Create a comparison manifest without rerunning models:

```bash
uv run --locked --extra benchmark sapi-lab benchmark-series reports/checkout-live-20261005/report.json reports/crm-new-run/report.json --output reports/two-task-series.json
```

The manifest preserves source/task/rubric/model/environment versions, budgets,
seeds and per-stage denominators. Two public synthetic tasks are a pilot, not a
statistical reliability estimate or evidence of production-system safety. Author
and runtime tool restrictions and judge prompt-injection limits are unchanged
from the first documented case. All original UI workflows and user containers
remain outside the experiment.
