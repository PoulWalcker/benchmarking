# Authoring a static sapi-lab/v0 workflow

This is the authoring contract for an experimental YAML profile, not official
Sapiens YAML. Return one YAML mapping with no markdown fences or explanation.
Do not use tools, browse, inspect files or run commands. All task information
is supplied in this prompt. There is one attempt and no verifier feedback.

## Document fields

Required top-level fields: schema, spec_revision, workflow, activation, execution.
Optional top-level field for this experiment: actors. No other fields.

- schema: the string sapi-lab/v0.
- spec_revision: the string 06ddd3333109cea8a2cb3071609070d7a3c0d3ff.
- workflow: a mapping containing exactly id, revision, kind, inputs, steps,
  dependencies, acceptance and output.
- workflow.id: the logical identifier given in the task. revision: integer 1.
- workflow.kind: Pipeline for Script-only processes, Gantt if any step is LLM.
- workflow.inputs: concrete sample values described by the task, not a schema.
  Tests replace these values. Derive outputs from inputs; do not hard-code answers.
- workflow.acceptance: a nonempty human-readable description of correctness.
- workflow.steps: a nonempty list of step mappings described below.
- workflow.dependencies: a list of two-element lists [upstream_id, downstream_id].
  These edges determine order. The order of entries in steps does not.
- workflow.output: a reference, or a literal object/array containing references
  and values. For example, `{first: {ref: steps.a}, second: {ref: steps.b}}`
  returns both results after all terminal steps finish. Step list order has no
  evaluation restriction; dependencies determine execution order.

## Steps and references

Each step requires id, kind, uses and with. Optional fields are when, join, actor.
IDs must match ^[a-z][a-z0-9_-]*$ and be unique. Use your own step IDs.
kind must match the selected operation's kind. uses is an operation name from
the supplied catalog. with is a mapping whose keys are exactly that operation's
listed input names, with a value or reference for each.

Reference syntax is a mapping with one key: {ref: inputs.FIELD},
{ref: steps.STEP_ID.FIELD}, or {ref: steps.STEP_ID} for the whole result object.
Every referenced producer must be an ancestor through dependencies. Values may
be nested objects or arrays containing references. To project a closed input
object, use `{id: {ref: inputs.ticket.id}, text: {ref: inputs.ticket.text}}`.
This constructs an object from two existing references; it introduces no
expression language, transformation, or new operation.

A condition has the form when: {ref: steps.STEP_ID.FIELD, eq: VALUE}.
It compares a value for equality. If false, the operation is skipped.
Reading a conditional step's result requires {optional_ref: steps.STEP_ID}
(or a field path). It returns null if that step was skipped. A missing producer envelope is an
error, not an optional skip. For a multi-stage conditional region, every step
may reuse an unconditional ancestor's scalar decision as its guard. The guard
is evaluated before operation inputs. Do not guard on a possibly skipped
producer or use optional_ref inside when.

Any step with two or more direct predecessors requires join: all_terminal.
This waits for both completed and skipped predecessors. Errors abort the run.
Two independent steps have no edge between them and consume their own inputs.
They can both be predecessors of a later join. Independence does not require
simultaneous wall-clock execution.

An actor field names a top-level actors entry. actors is a mapping from actor
name to {context_scope: STRING, allowed_operations: LIST_OF_OPERATION_NAMES}.
Every assigned operation must be allowed by its actor. Actors are metadata in
this experiment, not separate processes. Tasks may require particular actor names.

## Activation and execution

Use a Callback activation with exactly these fields:
kind: Callback; rule_id: a nonempty descriptive identifier; hook: a descriptive
event name; condition: valid_input; reaction: Trigger; workflow_ref: a mapping
containing id and revision matching the workflow exactly.
The test supplies inputs and starts execution directly; no callback is registered.

execution contains exactly concurrency: independent, deadline_seconds: 120,
and on_step_error: fail. These are required settings for this experiment.
Cycles, refinement, lifecycle, dynamic steps, mandatory parallelism, retries,
custom JavaScript and new operations are outside this experiment.

The catalog includes operations for other experiments. Select only operations
needed for the task; the available catalog does not require using every entry.
