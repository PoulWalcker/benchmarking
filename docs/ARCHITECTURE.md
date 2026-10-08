# Architecture

## One flow

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
     |
  AUTHOR (optional)
```

`coordinate/` selects scenarios and cases, binds fixtures, enforces budgets, stages Harbor task packages and sequences the stages. Harbor isolates each trial; it is orchestration around the flow, not part of it. The boundary that matters is **definition -> artifact -> evidence -> verdict**.

## Stages

| Stage | Location | Owns | Never |
| --- | --- | --- | --- |
| Shared | `src/sapi_config_lab/*.py`, `bindings.yaml` | profile validation, backend contracts, evidence encoding, pinned sources, wrapper stderr audit | import a stage |
| Author | `author/` | model-written or repaired YAML | compile or score it |
| Compile | `compile/` | validated YAML -> n8n JSON + step map | start n8n or decide acceptance |
| Execute | `execute/` | n8n runs, Agency bridge, the hosted trial host and environment providers, Harbor/Docker host tools and host configuration | turn engine success into acceptance |
| Evaluate | `evaluate/` | scoring recorded evidence (hosted provider evaluators, lifecycle test decision, review export) | rerun a workflow |
| Coordinate | `coordinate/` | CLI, scenario registry, runs, ledger, packaging, live gates, lifecycle controller | hold stage logic another stage owns |
| Verifier | `verification/` | independent acceptance and rubric | import compiler, runtime or coordinator code |

`tests/test_boundaries.py` enforces these import edges. Nothing imports `coordinate/`.

## One scenario registry

Every benchmark is `benchmarks/NN-<name>/`. Its `scenario.json` states what the scenario needs; there is no second registry in code.

| Field | Meaning |
| --- | --- |
| `environment` | `fixtures` (verifier-planned cases in the container) or a hosted provider name (`autowfbench`), served by the host per trial |
| `evaluator` | `verifier` for fixtures, otherwise one the environment's provider entry allows (`autowfbench`); checked on load |
| `default` | required boolean: selected when a command gets no `--scenario`; write `false` explicitly otherwise |
| `provenance` | AutoWFBench config: `{source, challenge}` of a pinned upstream task; `provenance/<source>-source.json` pins its bytes |
| `workflow_id` | the workflow id an author must use (hosted tasks) |
| `bindings` | a scenario-owned operation catalog instead of `src/sapi_config_lab/bindings.yaml` |
| `budgets` | `authoring_attempts` (0 refuses authoring), `runtime_model_calls` |
| `output` | `artifact_field`/`artifact_name`: an output submitted verbatim as a named file |
| `controls` | `reference_reward`: the reward the oracle must reproduce; declaring it requires the hosted evaluator to produce a scored quality, and without it hosted controls gate on acceptance |
| `prompt_extension`, `fresh_fixtures`, `human_review` | authoring prompt section, per-run fixture overlay, live report flag |
| `harbor` | trial resources rendered into `task.toml`; a verifier timeout must contain the verifier's plan |

Files beside it: the reference `config.yaml`, the container `instruction.md`, and either `task.md` + evaluator-only `cases.json` and `evaluation/` (`contract.json`, optional `rubric.json`) (fixtures) or `authoring-notes.md` + `bindings.yaml` (hosted). Scenario-owned evaluation facts live in `evaluation/` as data; the mechanisms that read them stay in `verification/`. Its `fixture_evaluators.py` composes independent business checks, procedure selection, guarded-call expectations, rubric views, corruption probes and optional freshness. It is an evaluator implementation table, not scenario discovery. Generic planning/scoring/generation has no benchmark-name dispatch; a new fixture adds benchmark data, independent evaluator code and one entry, plus tests.

Where a benchmark came from is provenance data. It selects which pinned bytes are trusted, never which code path runs; the code path follows `environment` and `evaluator`.

## One run mechanism

Every Harbor experiment (`harbor`, `generate`, `live`) runs through `coordinate/runs.py`:

- a new report directory, a source manifest, a pinned base image identity and staged task packages;
- each phase re-checks sources, image and pinned inputs;
- `Run.harbor` serves hosted tasks: a `TrialHost` per task on the host, credentials only in a per-job copy, and a leak scan over everything persisted;
- `Run.bridge` runs the budgeted Agency bridge;
- the report is always written, cleanup errors included.

Paid work is reserved in `coordinate/ledger.py` before it starts; an unknown outcome is never released.

## Hosted providers

A scenario has one of two placements (`scenario.hosted`): in the container (fixtures and the independent verifier) or served by the host per trial (any other `environment`).

- `execute/hosting.py` is provider-neutral. `TrialHost` is the trusted trial endpoint: `/begin` and `/finish` behind a per-trial token, a deadline timer, and the evidence files `environment-evidence.json`, `transport-evidence.json`, `native-record.json` and `trial.json` (which names its scenario). Timeout and abort persist all available trusted evidence without dispatching evaluation; an absent worker record is unknown, never fabricated success. A trial lock prevents finish/timeout/close from rewriting terminal evidence. Exactly one hosted execution attempt is supported per fresh session. It drives one `EnvironmentSession` per trial, a Protocol: `connection`, `limit_seconds`, `identity`, `finalize()`, `transport_evidence()`, `close()`.
- `coordinate/providers.py` is the one place names become provider code. `ENVIRONMENTS[name]` is a `HostedEnvironment` (allowed `evaluators`, `limit_seconds`, public `task` text, `start` returning a session, host-only `modules`); `EVALUATORS[name]` is a `HostedEvaluator` (`judge_calls`, `timeout_seconds`, `prepare`, `reevaluate`, `modules`). `host_only_modules()`, the generic host modules plus every provider's, is scrubbed from hosted containers.
- A provider owns its world (starting and seeding it, its tool listener, `finalize()` evidence, limits, identity) or the scoring of its recorded evidence, and reads its own scenario config. The core (`runs.py`, `packages.py`, `evaluation.py`, `controls.py`, `live.py`, `scenarios.py`) owns the run lifecycle, staging, budgets, ledger, leak scan, result shape and control rules.
- AutoWFBench is one provider: `execute/autowfbench.py` (the pinned upstream world behind a candidate tool listener with receipts and an ambiguity policy) and `evaluate/autowfbench.py` with `judge_calibration.py` (its pinned upstream scorer and judge). It reads `provenance`.

`tests/test_boundaries.py` keeps the seam: only `coordinate/providers.py` and the provider modules themselves import provider modules; other coordination and shared modules never name a provider, except `coordinate/cli.py` (help text) and `coordinate/provenance.py` (the source-manifest path), which name it as data only, an allowlist that may only shrink. `tests/test_hosted_provider.py` adds a fake provider through table entries alone, then stages its package and runs a real `TrialHost` trial through `Run.harbor`.

## Evaluation

`coordinate/evaluation.py` gives every evaluator one result shape: execution, acceptance and optional quality (`null` is not zero). Control rules branch only on hosted versus verifier. Hosted results are shape-checked immediately after evaluator dispatch; re-evaluation receives explicit judge options rather than an argparse namespace. Hosted trial/report fields `native_execution` and `terminal_completion` distinguish engine success from valid timely submission. The legacy AutoWFBench `result.execution` still means terminal completion; acceptance remains the evaluator's independent decision, and other evaluators need not require narrative output. Older records lacking the added fields remain readable with those observations null.

- **Verifier** (`verification/`, plus the scenario's `evaluation/` data packaged beside it): `plan` states which definitions must run, `coordinate/observe.py` runs them and records evidence, `evaluate` checks the record is exactly that plan and judges it. It is packaged into each task, and deliberately duplicates business rules instead of importing the implementation under test.
- **Hosted evaluators** (`EVALUATORS`): run on the host against a trial's recorded evidence; `hosted_evaluation` dispatches to the scenario's evaluator and writes `evaluation/report.json`, and `sapi-lab evaluate` re-evaluates a recorded trial through the same entry. AutoWFBench's pinned upstream scorer and judge are one hosted evaluator.

## Evidence

Execution evidence is written once; evaluation writes separate derived files beside it. `reports/` holds local runs (ignored). `evidence/` holds committed extracts that durable claims cite; they are never rewritten.

Pinned inputs are explicit and fail closed: the upstream source manifest under `provenance/`, the Harbor and n8n versions in `execute/`, the image identity, the source manifest of a run, prompt hashes in `tests/test_packaging.py`, and the inspected model-wrapper identity before live dispatch.

## Backend boundary

`WorkflowBackend` (`contracts.py`) is the seam between workflow semantics and an engine; n8n is the only implementation. A second backend must bring its own capability subset, compiler, native evidence and provenance checks. Do not add a universal IR, registry or inheritance tree before that second implementation exists.

## Terms

| Term | Meaning |
| --- | --- |
| definition | validated `sapi-lab/v0` workflow YAML |
| artifact | compiled backend workflow (n8n JSON) |
| execution | one engine run and its native evidence |
| acceptance | independent pass/fail verdict |
| quality | optional rubric or judge score |
| oracle / nop | positive control (reference submission) / negative control (no submission) |
| scenario / case | one benchmark directory / one fixture input of it |
| hosted | a scenario whose `environment` is a provider, not `fixtures`: the host serves its world and runs its evaluator per trial |

## Migration ownership constraints

The approved migration targets `BENCHMARKS -> CORE -> HARBOR`, with the conceptual
flow `benchmark -> compile -> execute -> evidence -> evaluate -> result`.
Benchmarks will own operations, world semantics and independent business scoring;
core will own generic workflow semantics, immutable evidence and experiment policy;
the Harbor integration will own task translation and invocation of pinned Harbor
0.21.0. Evaluation must continue to read evidence without rerunning workflows, and
independent expected-value checks must not import the implementation under test.

These are migration constraints, not a description of completed extraction. The
stage model above remains the current implementation. In particular, the global
catalog/operation bundle, provider and fixture-evaluator tables, digest acceptance,
TrialHost lifecycle, image scrubbing and copied Harbor job trees still exist.
Nothing in the baseline ticket removes them or establishes separate-verifier parity.

`MigrationOwnershipTests` in `tests/test_boundaries.py` rejects new imports of
`benchmarks` or the explicitly identified legacy business/provider modules from
application or verifier code. It also rejects additional direct Harbor imports
outside `sapi_config_lab.harbor_integration`. The exact existing 25 source/target
edges are frozen in the [baseline import snapshot](../evidence/migration-01-baseline/legacy-imports.json).
An exception belongs to that edge, never to an entire directory or future module.
Removing an edge is allowed; adding a caller is not. Static absolute/relative and
literal dynamic imports are checked, including imports nested inside functions.
These checks do not claim to detect arbitrary computed imports, business logic
embedded in JavaScript, file reads or name dispatch; those remain explicit migration
work. Existing stage checks continue to enforce verifier independence.

The [ticket 01 baseline index](../evidence/migration-01-baseline/INDEX.md) records
source/prompt identities, control observations, deadline and historical-format
inventories, permitted comparison normalization and the limits of the evidence.
Use fresh controls after changing source/package/image identity. Preserve the
snapshot; it is not an allowlist to expand when a later ticket needs a new edge.

## Versioned benchmark loading

`benchmark.py` is the neutral, metadata-only descriptor boundary for
`sapi-lab-benchmark/v1`. `discover_benchmarks(root)` validates versioned manifests
under an explicit search root; `load_benchmark(root, directory)` selects one.
Neither imports evaluator code or uses a provider table. Public files, trusted
files, the reference and declared `_shared` dependencies have disjoint,
file-by-file destinations. Paths are relative, contained and free of symlinks.
Business `config` stays opaque and deeply immutable. Native Harbor configuration
is a declared TOML file; validating its Harbor schema belongs to the integration,
not this loader.

`benchmark_loading.py` is the sole neutral dynamic-import exception in the stage
checks. `freeze_identity(descriptor, options)` hashes the complete declared closure
at staging, including the manifest and dependency files, and freezes JSON options.
`load_entrypoints(descriptor, identity)` rechecks it before importing the selected
trusted entrypoints. Its namespace combines source identity with checkout location,
so identical filenames or IDs in different roots cannot reuse each other's modules.
Relative local imports read only declared trusted Python bytes captured at loading;
undeclared siblings cannot enter through the local import path. The loaded snapshot
continues to use those captured bytes for delayed imports. This is trusted-code
loading, not a sandbox for evaluator code or an identity for external installed
Python dependencies; the environment's dependency pins remain separately owned.

The concrete synchronous callable contracts are `plan(submission: Path, options:
Mapping) -> Mapping`, `evaluate(evidence: Path, options: Mapping) -> Mapping`, optional
`prepare(context: Mapping) -> RunBinding`, and optional `snapshot(context: Mapping)
-> Mapping`. Plans/verdicts retain existing document schemas; the loader checks
callable arity but does not execute or validate their business results. Callers pass
selected descriptor/configuration and seed/mode options; no candidate-controlled
callable paths are accepted. Recorded identities can be checked before re-evaluation;
this ticket does not migrate existing re-evaluation consumers.

`coordinate/benchmark_discovery.py` supplies metadata-only legacy headers for
`sapi-lab benchmarks`. It neither translates a legacy header into an executable
versioned descriptor nor imports `coordinate/scenarios.py`. The latter retains
legacy execution globals for unmigrated commands and skips versioned manifests;
new consumers must use the explicit descriptor boundary. All eleven existing
benchmarks still use the legacy execution path. Verifier independence and the
frozen migration ownership edges are unchanged.
