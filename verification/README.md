# Independent acceptance checks

`verify.py` composes two independent checks. `business.py` accepts an
engine-neutral `WorkflowObservation` and checks business outputs, intermediate
values, routing and source facts. `n8n_provenance.py` separately ties that
observation to native n8n records, including persisted success, node envelopes
and execution timing. Both checks must pass. The business checker imports no
compiler, operation or engine implementation.

The verifier uses `src/sapi_config_lab/runtime/execution.py` to compile and
execute each case through the selected backend. It supplies fixture inputs but
never expected answers to the runner. Only the n8n evidence adapter is currently
implemented: new versioned reports require matching `n8n` engine and native
evidence metadata. Historical unversioned records retain their strict legacy
checks. An unfamiliar engine cannot gain acceptance through flat n8n aliases.

| Scenario | Positive runtime cases | Invalid runtime inputs |
| --- | --- | --- |
| Invoice total | 38000 AED; alternate values including zero and EUR; largest safe integer total | Empty; duplicate IDs; mixed currencies; negative; fractional; unsafe amount; aggregate overflow |
| Ticket routing | High at 3 and 12 days; normal at the 2-day boundary and at zero | Negative and fractional overdue days |
| Competitor report | Original sources; alternate sources with distinct marker strings | Empty product and empty marketing material |

For every successful run the verifier checks actual import/execution IDs,
pinned n8n version recorded by the runtime, raw Result node data, one logical
event per required step occurrence, operation-specific node envelopes and statuses,
input preservation and n8n node start/end ordering. Python integer arithmetic
provides the independent invoice oracle. The stated `> 2` condition provides
the routing oracle; the other branch must be skipped and absent from step
outputs. A guarded Code wrapper may execute for a skipped logical operation.

Research checks both independent node executions, absence of cross-branch
dependency data, actor metadata, a combine node that starts after both finish,
and unchanged analyses/evidence through join and writer. Stubs have exact
expected prose. Live responses allow nonempty source excerpt substrings and
different English prose, with bounded checks for named capabilities, audiences
and channels from each fixture. These lexical checks catch omitted facets and
an unrelated report; they do not prove complete factual or semantic quality.

Each stub task also rejects three invalid definitions (unknown operation,
cycle, unsupported mandatory parallelism). It deliberately mutates generated
Result code, successfully imports and executes the broken workflow in n8n,
and requires acceptance to fail. Copies of genuine execution records are
corrupted (wrong final data, absent Result, false trace, missing analysis,
contentless report with retained evidence) to test the verifier itself. These
copy mutations are labelled separately from actual n8n executions.

The Harbor task's `tests/` directory contains the verifier modules and case data.
`tests/test.sh` initializes reward to zero, then writes one only if all checks
pass. Oracle solutions copy the supplied base YAML to the submission path;
the no-op agent creates no submission and must score zero. This first suite
validates YAML→compiler→real n8n execution, not prompt-to-YAML synthesis.
The source fixtures are visible in this repository: they are test-only task
inputs, not a confidential held-out benchmark against a malicious agent.

Harbor packages are assembled automatically from these canonical files before
every run. To inspect a package without running it, choose a new directory:

```bash
uv run --locked sapi-lab package-tasks /tmp/sapi-tasks
```

The verifier writes `report.json` and per-case artifacts beneath the requested
report directory. In Harbor that directory is `/logs/verifier`. The adapter
saves source inputs, generated JSON/map, import/execute logs, raw and persisted
execution records, and a case manifest. Compilation errors, expected runtime
input rejection, successful runtime execution and acceptance rejection remain
distinct in the report. New case manifests use `sapi-lab-execution/v1`:

- `execution.succeeded` describes engine completion, while `acceptance.passed`
  remains null until the verifier records a decision. A mutated workflow can
  execute successfully and still receive rejected acceptance.
- `input.source=fixture` identifies injected input separately from the real
  execution engine.
- `llm.selected_mode` records the option selected at compile time.
  `agency_http_call_count` counts native Agency HTTP node executions, including
  failed attempts; it does not establish downstream provider calls.
  `provider_call_count` is null when upstream evidence is unavailable.
- `evidence.engine` points to raw native records; `evidence.workflow_trace`
  labels the generated workflow's authored trace. The trace alone cannot prove
  execution.

`report.json` uses `sapi-lab-verification/v1`. Its top-level `passed` and each
case's `passed` concern the test expectation: an expected rejection can pass a
test while `acceptance.passed` is false. Existing flat fields remain for report
consumers. Historical reports are not migrated or rewritten.

`SAPI_LLM_MODE=live` and `SAPI_BRIDGE_URL=...` select the separate live suite.
Live cases are three Script-only invoice batches, ticket high/boundary cases,
and two research source pairs (eight configured LLM operations). Observed
Agency requests are counted separately from provider calls. Their copied
definitions receive a 600-second execution deadline. Stub diagnostic
negatives and broken generated workflows are not replayed against the model.

Out of scope: Sapiens harness, actor process isolation, correction loops,
daily digest lifecycle, durable retries, performance guarantees, and
guaranteed concurrent progress.

## Expansion contracts

The four opt-in compositions use `scenario_contracts.py` for independent
role/input-origin obligations and `scenario_business.py` for arithmetic,
routing, honest draft review and bounded source-fact checks. Observations are
keyed by step ID, so two invoice chains retain six distinct occurrences.
`roles.py` binds arbitrary submitted IDs to obligations and rejects missing,
extra, swapped or ambiguous lineage. Engine provenance remains separate.

`cases.json` includes positive, negative and metamorphic inputs; private run
overlays change values and markers after public prompt freeze while preserving
these criteria. Live execution admits only each scenario's two declared
`live_cases`, with `SAPI_CASE_NAME` selecting one grant at a time. See the
[expansion guide](../docs/SCENARIO-EXPANSION.md) for commands, caps and limits.

## Rubric evaluation

`rubric.py` scores a weighted card and `rubric_cards.py` holds the cards.
`rubric_facts.py` is the seam between them and acceptance: `verify.py` collects
one set of named checks and judge prose per executed case, then writes the
returned document to `evaluation.json` beside `report.json`.

A named check calls an obligation `business.py` or `scenario_business.py`
already states and records raised / did not raise. The rule is never restated,
so a check cannot drift from the acceptance it describes. The rubric reads the
verdict and never sets it: acceptance stays binary, `reward.txt` still comes
from the verifier's exit status alone, and the score rides alongside it.

Seven scenarios have cards, each weighing ten points:

| Scenario | Deterministic | Judged |
| --- | --- | --- |
| `ticket-routing` | 10 over four named checks | none: its only authored output is a classification acceptance pins exactly |
| `competitor-report` | 6 over five named checks | 4: synthesis, invention |
| `support-review-packet` | 6 over three named checks | 4: usefulness, honesty, actionability |
| `bulletin-market-brief` | 6 over five named checks | 4: article reporting, concision |
| `priority-support-brief` | 6 over two named checks | 4: invention, self-containedness |
| `invoice-total` | 10, one pass/fail check | none |
| `dual-ledger-closeout` | 10, one pass/fail check | none |

`revise-answer` and `daily-digest` have no card: they are verified through
`extensions.py` and `lifecycle_submission.py`, which produce no
`WorkflowObservation` and so never reach this seam. They write no
`evaluation.json`.

A card with judged criteria is scored only when a judge is reachable, and there
is none in the container: the document there states `not_evaluated` with its
reason and a null score, never a zero and never a verifier failure.
