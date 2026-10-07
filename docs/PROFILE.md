# `sapi-lab/v0` profile

`sapi-lab/v0` is the workflow contract implemented by this research project.

It is a bounded experimental interpretation inspired by the pinned Sapiens source, not an implementation of the complete Sapiens specification.

The source of truth is the validator/runtime code. This document records the durable semantics that are non-obvious to a workflow author.

## Core semantics

| Area | Supported behavior |
| --- | --- |
| Identity | workflow has an `id` and positive `revision`; activation targets that exact revision |
| Workflow kinds | `Pipeline` and `Gantt` with the currently validated step restrictions |
| Operations | `uses` selects an explicitly registered operation from `bindings.yaml` |
| Inputs | fixture values are supplied through `workflow.inputs`; this is not a general input-schema language |
| References | values may reference inputs or outputs of ancestor steps; a step result has exactly its declared output fields, so an object copying all of them with `ref` is that whole result (`{ref: steps.ID}`); with `optional_ref` it is not, since a skipped producer gives null |
| Dependencies | explicit edges define order; YAML file order does not |
| Conditions | `when` uses exact equality over supported scalar references |
| Skip | false condition marks a step skipped and does not call its operation |
| Optional references | may resolve null for a skipped producer; missing unexpected data is still an error |
| Join | `all_terminal` waits for listed branches including skipped branches |
| Concurrency | independence does not guarantee parallel execution; required parallelism is not supported by the current backend |
| Cycles | ordinary workflow dependencies are a DAG |
| Errors | fail-fast; no general durable transport retry policy |
| Output | workflow output resolves after terminal dependencies complete |
| Actors | logical operation permissions; not isolated long-lived Sapi processes |
| Activation | supported Callback/Cron subset used by the lab lifecycle path |
| Acceptance | human-readable workflow acceptance is not trusted by itself; executable verifier logic is separate |

Execution uses one logical envelope containing inputs, step outputs/statuses, events, and simulation metadata. The profile does not model one n8n item per business record.

## Operation catalog

`src/sapi_config_lab/bindings.yaml` is the operation catalog for project scenarios.

A new operation requires:

1. an explicit input/output contract;
2. an implementation;
3. a catalog binding;
4. tests and verifier coverage where the operation changes observable behavior.

The workflow profile does not infer hidden capabilities from arbitrary code.

## Refinement

Bounded refinement repeats one graph region with feedback until acceptance or exhaustion.

The current implementation:

- has an explicit maximum attempt count;
- includes the first attempt in that count;
- propagates selected prior result/feedback through declared carry state;
- stops later attempts after acceptance;
- fails the final workflow result on exhaustion;
- lowers the bounded loop into native graph structure rather than creating an unbounded runtime loop.

Refinement is part of workflow execution semantics. It is different from the durable candidate lifecycle below.

## Candidate lifecycle

Lifecycle manages workflow definitions across executions: candidate registration, testing, repair/rebuild, release, scheduling, archive, and suspension.

It is outside the compiled candidate graph.

The current lifecycle implementation supports a narrow explicit policy set, including bounded rebuild count and a restricted daily Cron form. Definitions are immutable by hash/revision; a repair creates a new candidate rather than mutating the tested definition in place.

Do not interpret lifecycle support as a general distributed scheduler or full Sapiens WBS runtime.

## Live LLM transport

In live mode an LLM operation is represented in n8n by transport nodes around the external Agency bridge.

A runtime request identifies the logical invocation, operation, actor, and validated inputs. The response must match the invocation and declared output contract.

Important properties:

- model/provider selection belongs to the external wrapper, not the workflow catalog;
- runtime call counts are admitted before execution;
- skipped guarded LLM steps do not dispatch;
- malformed/mismatched responses fail the run;
- original workflow context is restored from trusted pre-request state, not from arbitrary response fields;
- live execution has explicit deadlines;
- there is no general transparent retry layer for ambiguous model outcomes.

## HTTP tool operations

A catalog binding with `transport: http` calls a tool of the run's environment, which a hosted scenario serves through a run-scoped, authenticated tool listener in front of its environment provider's world. Workflow semantics do not change: the definition still compiles to the same DAG, the operation URL and token are bound at run time, and environment administration, finalization and scoring stay on the trusted host. HTTP tool operations are not supported inside refinement.

## Validation boundaries

The validator checks the supported subset, including:

- known operations and allowed step kinds;
- unique IDs and expected YAML keys;
- dependency endpoints and DAG structure;
- reference ancestry;
- output-field availability at the supported depth;
- explicit joins;
- actor restrictions;
- activation revision;
- supported refinement/lifecycle fields.

It is not a complete JSON Schema engine or a general semantic verifier.

The project does not currently guarantee:

- arbitrary nested schema validation everywhere;
- LLM answer truthfulness;
- full ReactionPolicy semantics;
- dynamic collections of workflow steps;
- durable general retries;
- real actor memory/context isolation;
- arbitrary cycle semantics;
- executor-independent native provenance.

## Backend support

n8n is the implemented production backend.

The compiler may reject a valid profile feature when the backend cannot preserve its semantics. That is preferable to silently compiling different behavior.

A future backend must define capability support and native evidence explicitly; passing the common Python protocol alone is not proof of semantic equivalence.

## Specification provenance

The repository pins the Sapiens source revision used as the conceptual baseline and keeps the public source snapshot under `provenance/`.

Changing project code does not automatically change the upstream specification pin. Update the pin only when the project intentionally adopts a different external source baseline and can explain the semantic difference.

The model-facing authoring rules in `generation/FORMAT.md` must remain compatible with this profile. Because that file participates in prompts, changing it also changes future prompt hashes and should be treated as an experiment change, not copy editing.
