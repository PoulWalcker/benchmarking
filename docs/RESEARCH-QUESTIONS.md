# Questions for discussion and research

Research questions from October 4, 2026, followed by current task status.
The questions do not by themselves authorize new experiments.

Prepared and model-authored YAML have now run through real n8n, including
separate live replays and four bounded catalog-composition tasks. Refinement and
both the unchanged and separately repaired digest's Callback/Cron paths also have
native live evidence. Two further composition tasks passed all four one-shot
authorings and four live cases with 12 runtime calls.
See [recorded results](history/RESULTS.md) and the [current evidence table](../README.md#evidence-and-limits)
for the exact scope. Arbitrary workflow generation and compatibility with a
Sapiens executor remain open.

## Model-authored YAML

- Can a model independently produce correct YAML from a task description,
  using the format description and available operations?
- Can it solve new tasks that were not used to develop the examples and compiler?
- How should we evaluate config correctness, execution success, and final
  task correctness separately?

## The role of bindings

- How necessary is a separate operation catalog? Which information belongs
  there, and which belongs directly in a task's YAML?
- Who creates and maintains the catalog: a developer, a model, or both?
  Where do operation descriptions and their actual implementations come from?
- Is most of the solution already encoded in specialized operations?
  Can general tools solve new tasks without adding an operation per scenario?

## Workflow and compiler complexity

- How difficult is extending the compiler to support loops, result correction,
  waits, external events, and recovery after failure?
- Where do the supported semantics of our YAML end and executor-specific
  limitations begin?
- How can we evaluate more steps separately from new behavioral rules?
  A large static graph and a small process with a loop are different questions.

## Alternative executors and Sapiens

- Can Sapiens itself execute processes described by our YAML? What is actually
  implemented in its code and harness, and what exists only in the specification?
- Would Sapiens need a separate adapter or compiler? Which concepts could be
  transferred without changing their meaning?
- Can the same config execute in n8n and another environment with identical
  observable behavior? How should this be tested?
- Which alternatives should be investigated: a custom Python executor,
  LangGraph, Temporal, or Prefect? No choice has been made, and none is assumed
  to support our YAML out of the box.

## Verifying new tasks

- Who defines correctness criteria for a new task? How can verification be
  independent of the model that creates the config and solves the task?
- Which conditions can be checked automatically from execution data, and
  which require judging the meaning of the answer?

## Semantic equivalence and exact topology

The SC-03 preview passthrough exposed the difference between task correctness
and a prescribed topology. Its stopped series and later exact-edge contract
remain unchanged. Two new [composition tasks](GENERALIZATION-EVALUATION.md)
accept safe sharing, inline objects, serialization and redundant dependencies
while checking source origins and native execution. Manual native controls show
different accepted compositions. Both model answers per task chose the same shape;
the two-audience task's shared-product/inline-writer graph adds a third accepted
shape beyond its two manual controls. All four authorings and four selected live
cases passed. This does not solve general program equivalence; the remaining
question is how far such acceptance can extend without losing business rules.

## Clarity of execution reports

Reviewing the original invoice example's locally preserved `case.json`
revealed four ambiguities. This is our report format: it combines n8n data,
the generated workflow's result, and information from the test runner.

- `status: success` means execution passed the runner's checks. It does not
  mean the independent verifier accepted the task solution. How should these
  two outcomes be distinguished and their reports connected?
- The compiler automatically sets `result.simulation: true`, although the run
  uses real n8n. What exactly should this field mean? Should it be renamed,
  clarified, or removed?
- `llm_mode: stub` describes the run setting, although the invoice scenario
  makes no model calls. How should reports separately show the selected mode
  and the actual presence and number of LLM calls?
- `run_data` contains native n8n node execution data. `result.trace` and
  `result.statuses` are produced by generated workflow code. How should their
  provenance be labeled so workflow-authored records are not mistaken for
  independent evidence of correctness?

These observations do not invalidate execution evidence. Current v1 records
address the distinctions through explicit execution, acceptance, input, LLM,
and evidence fields; see [the report field guide](REPORTS.md). Historical records
keep their original format and are not retroactively rewritten.

## Task status, October 4, 2026

| Work | Current evidence and next question |
| --- | --- |
| [Generated YAML with live operations](history/generated-yaml-live-execution.md) | Seven frozen-replay cases accepted with eight runtime wrapper calls. The immutable overall report retains its container-preservation failure; the user-confirmed service stop is documented separately. |
| [Four-task expansion](history/scenario-expansion.md) | All four selected families have native live evidence across the original and corrected series. The original SC-03 rejection remains preserved. This measures bounded use of existing operations. |
| [Bounded refinement (04)](EXTENSION-ACCEPTANCE.md#reply-refinement-04) | Three one-shot YAMLs accepted. Selected live positive passed its first draft; three real attempts exhausted the impossible limit with no accepted output. Rejection then acceptance was observed only with native stub controls. |
| [Digest lifecycle (05)](LIFECYCLE-EVALUATION.md) | Completed with one authoring, five runtime and one WBS repair call. Unchanged authored revision passed Callback/actual-clock Cron; a separate intentional mutation was rejected, then model repair passed new Callback/actual-clock Cron. Fixed-minute evaluation schedules and an external durable controller were explicit. |
| [Local n8n UI](N8N-UI.md) | Implemented and manually demonstrated: new inactive workflow, execution 61, four correlated runtime calls, six existing workflow hashes preserved. Foreground helper stopped; another execution requires a fresh grant. |
| [Pinned-source maintenance](SPEC-SOURCE.md) | Offline verification and explicit fetch/diff are implemented. The recorded pinned-revision fetch matches vendored bytes; the specification pin is unchanged. |
| [Two new composition tasks](GENERALIZATION-EVALUATION.md) | Four untouched one-shot YAMLs and four selected native live cases passed, using 12 runtime calls under the 14-call cap. Manual controls and the model-authored two-audience graph demonstrate specific accepted alternatives; both model attempts per task used the same shape. Catalog and compiler were unchanged. |
| [Sapiens harness feasibility](history/sapiens-harness-feasibility.md) | Investigation prepared; no harness integration or Sapiens workflow executor demonstrated. Computer use and an alternative executor remain outside these experiments. |

The [independent agent review](../reports/20261004-open-tasks-integration/INDEPENDENT-REVIEW.md)
separates technical evidence from prose quality. The [content review](../reports/20261004-open-tasks-integration/AGENT-QUALITY-REVIEW.md)
found the earlier short research outputs supported, while SC-02 correctly rejected
overlong support drafts. It is agent review, not human sign-off. The earlier
third-authoring and human-review criteria belong to a proposed team pilot;
they are not specification requirements or proof of a statistical success rate.

The original [missing-adapter diagnosis](../reports/20261004-imported-workflow-diagnosis/SUMMARY.md)
and dated planning documents remain historical evidence. Their preparation-time
status does not override the results above. Every new run still needs its own
frozen sources, acceptance checks and bounded evidence.
