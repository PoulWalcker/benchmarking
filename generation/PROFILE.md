# Experimental profile: sapi-lab/v0

This is a concrete experimental interpretation of the pinned Haskell notation's
leaf types, not an official Sapiens format. Config 04 uses bounded refinement
compiled into one native graph. Config 05 requires the durable lifecycle
controller around its native candidate graph; importing that graph alone does
not implement lifecycle management.

## Field semantics

| Area | Rule |
| --- | --- |
| Identity | `workflow.id` and a positive `revision`; activation references exactly this pair |
| Workflow type | `Pipeline` contains Script steps; `Gantt` allows Script and LLM steps |
| Operation | `uses` selects an explicitly registered handler from `bindings.yaml` |
| Inputs | `workflow.inputs` contains fixture values, not a schema; `with` binds operation inputs |
| Data | `{ref: inputs.x}` or `{ref: steps.step_id.field}`; a step result is available only to descendants |
| Order | `[a, b]` means b waits for a; YAML entry order does not define dependencies |
| Condition | `when: {ref: ..., eq: ...}` uses exact equality; the examples compare scalar values |
| Skip | A false condition produces `skipped`; the operation is not called, but the bookkeeping envelope still propagates |
| Optional | `optional_ref` returns null only for `skipped`; a missing result remains an error |
| Join | `all_terminal` waits for all specified branches, including skipped branches; a step error aborts the run |
| Concurrency | `independent` allows sequential execution; `required_parallel` requires concurrent progress and is rejected by the demo backend |
| Cycles | Ordinary dependencies form a DAG by our choice; the new source specification requires an explicit cycle policy |
| Errors | Only fail-fast behavior is supported; transport retries are absent |
| Result | `workflow.output` is resolved after all terminal steps finish |
| Acceptance | A human-readable condition; a separate executable verifier is required |
| Actors | Logical assignments and allowed-operation lists; no real Sapi processes or context isolation are provided here |
| Activation | The profile supports Callback or Cron; the demo injects a fixture instead of admitting a real event |

Execution data is represented by one JSON envelope containing
`inputs`, `steps`, `statuses`, `events`, and `simulation`. Arrays remain inside
that envelope. This profile does not use one n8n item per input record.
At a join, results with the same name from shared ancestors must agree.

The n8n wrapper executes each guarded Code node but does not call its operation
when the condition is false. Merge append then collects the envelopes. This is a
deliberate choice in the demo backend: it does not generate native IF nodes or
wait for data from an inactive branch in stub mode.

In `stub` mode, LLM stubs in `operations.js` are deterministic. They do not execute
prompt text from bindings. Classification therefore demonstrates routing, and
the research report demonstrates data transfer; model quality is not evaluated.

## Bounded refinement and candidate lifecycle

`execution.refinement` describes repeating one graph region with feedback.
The `max_attempts: 3` limit includes the first generation. Success means `until`
is true; only an accepted attempt can produce the result. Exhaustion means failed.
`initial_state` and `carry` define how the result and feedback are passed on.
The compiler unrolls the bounded whole graph into native attempt copies with
IF continuation, separate invocation IDs and Checkpoint history. The same
deadline covers all attempts. Later attempts never run after acceptance;
exhaustion fails Result while native checkpoints remain available. The lowering
is independent of operation names and scenario IDs.

`lifecycle` is outside WorkflowDefinition: it describes candidate management,
testing, WBS rebuilds, archiving, and release. `max_rebuilds: 2` allows at most
three candidates, including the initial one. Cron is enabled for the exact tested
revision. Overlap, missed-run, and in-flight replacement policies are explicit.
A SQLite registry and `runtime.lifecycle.LifecycleController` implement these
policies. Definitions and their hashes are immutable. Callback events persist
before execution and deduplicate by rule/event ID. Test results release the
exact tested revision. A configured rebuilder receives the rejected candidate
and findings, returns a new validated fork, and cannot weaken the frozen inputs,
acceptance or lifecycle policy. Fork persistence, old-definition archive and
next test Callback commit atomically. Exhaustion suspends the family and records
Adhoc work. Restoring an archive neither replays nor releases it.

The supported Cron subset is one fixed minute and hour daily in an IANA timezone.
The foreground scheduler admits the current minute, skips missed or overlapping
runs, and deduplicates across restart. Runs retain their admitted revision;
candidate executions serialize, so replacements cannot interrupt in-flight
runs. Registered forks are distinguished from model-authored WBS rebuilds.
The latter preserve returned YAML and an append-before-dispatch call record.
Completed durable results can finish registry transactions after restart;
uncertain native/model outcomes suspend instead of silently repeating work.
See [the explicit lifecycle commands](LIFECYCLE.md). These are lab policies;
the pinned upstream specification is unchanged.

## Validator limits

Checks cover operation existence, step kinds, unique IDs and YAML keys,
dependency endpoints, DAG structure, reference ancestry, first-level output
fields, explicit joins, actor restrictions, exact activation revision, and
selected extension fields.

Checks do not cover full nested JSON Schema, LLM answer truthfulness,
complete ReactionPolicy semantics, dynamic step collections or durable retries.
Lifecycle admission additionally validates the supported daily Cron/timezone
and exact policies; it rejects other lifecycle strings. Invalid types in
some structures can raise an ordinary Python exception instead of a helpful
diagnostic. This is a narrow research validator.

Pipeline validation trusts the catalog: forbidding a hidden LLM call inside a
Script implementation requires reviewing its source and dependencies; `kind`
alone is insufficient. The package's operation implementations were inspected
and do not make network calls.

## Cost of extensions

A new scenario using existing operations and constructs needs new YAML.
A new operation needs a contract, implementation, and binding. New execution
semantics require changes to the profile, validator, runtime, and tests.
A new backend must implement supported constructs and reject the others.

The prototype's IR is an ordinary Python dict after validation. There is no
fully typed IR, Haskell parser, or independent second backend.

## Research harness transport modes

`compile_n8n(..., llm_mode="stub"|"live")` uses one profile and catalog.
`compile_demo` remains a compatible name for stub exports and unit tests.

In `live` mode, an LLM step expands into Prepare → IF → HTTP Request → Restore.
A false condition goes directly to Restore: the HTTP node does not execute,
the step has no output, and its status is skipped. Separate real n8n probes
check this behavior. Script steps retain their previous semantics.

An HTTP request contains `invocation_id`, `operation`, `actor`, and `inputs`.
The ID includes the logical revision/step and actual n8n workflow and execution
IDs. A response contains `invocation_id`, `status: completed`, `output`, and
optional `usage`. A mismatched ID, extra fields, an array instead of one response,
an HTTP error, or an output-schema violation fails the run. Original context is
restored from Prepare, not from the external service's response.

LLM bindings have `input_schema` and `output_schema`. The supported JSON Schema
subset is explicit: `type` (including unions with null), `properties`, `required`,
`additionalProperties: false`, `enum`, `items`, `minLength`, `minItems`, `maxItems`,
and `minimum`. The compiler rejects unsupported keywords. This is not a complete
JSON Schema validator. Script operations validate their domain inputs and exact
output field set; a nested schema is not implied for them. JavaScript `integer`
values are restricted to safe integers.

`Result` contains `output`, `trace`, `steps`, `statuses`, `workflow_ref`,
`spec_revision`, `llm_mode`, and `simulation`. The last field remains true because
inputs are injected as fixtures; `llm_mode` distinguishes live models from stubs.
Live deadlines are explicit; transport timeouts do not extend them.

The catalog does not select a model or credentials; the existing wrapper owns
those settings. Refinement is compiled; lifecycle and event deduplication belong
to the explicit controller. Neither path adds transport retries, actor memory
or a Sapiens harness. n8n's
`executionOrder: v1` is retained: independent analyses do not imply parallelism.
