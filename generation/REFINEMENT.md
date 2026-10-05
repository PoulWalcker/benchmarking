# Task-specific bounded refinement extension

This section applies only when the task explicitly requests bounded refinement.
It overrides the static format's prohibition of refinement and its closed
execution field list. All other static format rules and catalog contracts apply.

Add `execution.refinement` with exactly these fields:

- `region`: every logical step ID in the workflow, once each. Partial or nested
  regions are not supported; ordinary step dependencies remain a DAG.
- `initial_state`: a JSON object containing the state consumed by attempt one.
- `until`: `{ref: steps.STEP.FIELD, eq: VALUE}`. The referenced result must be
  available unconditionally. An attempt succeeds when this equality is true.
- `max_attempts`: an integer from 1 to 10, including the first execution. Use
  the task's specified limit; this is semantic revision, not a transport retry.
- `carry`: an object with the same keys as `initial_state`. Its values are
  literals or ordinary references evaluated after a rejected attempt; they
  become the next attempt's state.
- `exhausted`: `failed`.
- `output_policy`: `last_accepted_only`.

Operation arguments may additionally use `{ref: runtime.FIELD}` for a field
declared in `initial_state`, including nested fields when present. Each attempt
starts with fresh step results and consumes its carried state. Feedback must
come from the declared check's actual result, not an invented literal.

The compiler emits one complete n8n graph with bounded attempt copies and native
IF continuation. Acceptance stops before the next attempt. Exhaustion or any
step failure yields no accepted output. `workflow.output` is resolved from the
accepted attempt only. One declared workflow deadline covers every attempt.
Transport retries, arbitrary graph cycles, new operations and lifecycle are
still outside this task. The authoring model has one submission opportunity;
workflow refinement does not grant additional YAML-authoring attempts.
