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
- `Run.harbor` submits versioned packages through `harbor_integration/runner.py` directly into the report's `jobs/` tree. Harbor owns phase limits and resources; partial trial references remain in the report on failure. Legacy callers retain a `TrialHost` per hosted task, credentials only in a per-job copy, and a leak scan over everything persisted;
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

Checkout's native simulator mounts Harbor's verifier output and writes immutable
`world/` observations before tool dispatch and after receipts. Its semantic deadline
and explicit snapshot share one terminal decision; finalizer latency does not extend
the workflow window. Forced process termination can leave only earlier observations,
with no final worker record. Snapshot recovery preserves that absence as unknown and
never constructs missing environment evidence. These observations do not implement
container cleanup or automatic retries; those remain Harbor responsibilities.

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
catalog/operation bundle, provider and fixture-evaluator tables, legacy digest acceptance,
TrialHost lifecycle, image scrubbing and copied Harbor job trees still exist for legacy callers.
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
legacy execution globals and a temporary metadata view for migrated invoice
callers; new consumers use the explicit descriptor boundary. Invoice-total and checkout-recovery use positive packaging; the nine other
benchmarks retain legacy execution until their planned retirement.
Verifier independence and the frozen migration ownership edges are unchanged.

## Positive Harbor packages

`harbor_integration/tasks.py` adds a separate packaging path for versioned
benchmarks. It validates native `task.toml` against pinned Harbor 0.21.0. Integration
modules may import neutral contracts, generic execution mechanisms and each other;
benchmark planning and scoring stay outside.
Legacy task packaging remains for unmigrated benchmarks. Invoice-total and checkout-recovery use the versioned path.

`stage_benchmark` copies declared public files into `environment/payload`, public
and trusted files into `tests/payload`, and the reference into `solution/`. Public
`instruction.md` is copied verbatim to the task root. Shared dependencies retain
`dependencies/<alias>/` paths in each payload. Generated build recipes start from
`harbor_integration/runtime/Dockerfile`: only pinned upstream Alpine/n8n runtime
bytes enter its ancestors. No legacy image, checkout or private file enters the
public image and is later scrubbed. `inputs.json` hashes the source/options identity
and every staged file.

The initial profile is single-step Linux/Docker with an unprivileged author (UID
1000) and explicit separate verifier environment. The existing author submission
path is `/app/submission/config.yaml`. A native root collection hook opens this
file without following links, rejects executable/extra/non-YAML inputs and copies
bounded bytes to protected `/submission/config.yaml`. Harbor transfers that
snapshot after stopping the author. The verifier checks it again before trusted
`verifier.sh` runs. Missing submission remains missing; the benchmark owns its
acceptance decision. These are admission rules, not workflow evaluation or a new
trial lifecycle.

Harbor implicitly collects `/logs/artifacts`. This profile explicitly replaces
that entry with an all-excluded collection into a distinct host destination; the
author-writable mount is never the uploaded tree. The verifier rejects unexpected
conventional artifacts. Native settings must match this transfer profile. Prebuilt
images, injected credentials, external agent inputs and custom collection hooks
are unsupported in this initial path. This does not change the legacy runner or
claim other providers' isolation/resource behavior.

Harbor builds the verifier from `tests/` and skips subsequent test upload. The image
includes `/tests/test.sh`, its declared payload and dependencies. Build-time isolated
imports reject unresolved entrypoint imports before verification starts. No reference
enters the verifier image. Packaging does not invoke jobs or implement the benchmark's
compile/execute/evaluate pipeline.

### Invoice package execution

`benchmarks/01-invoice-total` owns its unchanged public catalog, trusted JavaScript
operation bundle as explicit compatibility material, case/probe planning,
independent totals, role contract/output schemas,
and deterministic rubric. Its explicit fixture object supplies these to verifier
mechanisms without consulting the legacy evaluator table. The old table and bundle
remain compatibility inputs for unmigrated callers. The versioned task includes
its own byte-identical operation bundle and no evaluator table. Keeping the full
bundle preserves execution of every operation the unchanged full catalog exposes,
including incorrect candidate graphs which independent invoice acceptance rejects. Ordinary and recursive refinement compilation accept trusted operation
source supplied by coordination, never an executable path selected in candidate YAML.

`coordinate/benchmark_packages.py` supplies a positive list of generic workflow and
verification sources to the separate verifier image. Integration copies and hashes
that list; it does not discover a checkout or copy host services. The declared
entrypoints and complete import closure are checked while building. The public
image still contains only declared public files and pinned upstream runtime bytes.

`coordinate/benchmark_worker.py` reads the trusted staged metadata, verifies source
hashes, binds both benchmark and generic verifier sources into evaluator identity,
calls the local planner, executes the observation plan with the supplied
bundle, and calls the local evaluator on frozen evidence. It writes the normalized
result beside the existing independent verification report. Harbor keeps ownership
of the two environments, collection, transfer and phase limits. Missing YAML yields
rejected acceptance, null execution/quality and deterministic reward zero.

Existing scenario callers temporarily receive a compatibility view from the
manifest's `config.legacy` section. This preserves selection and prompt arms while
`stage_tasks` chooses versioned materialization by descriptor presence. There is no
benchmark-name branch or new provider/evaluator registration.

Generation/selection temporarily retain the host-side `tests/cases.json` record and
its historical hash format. This compatibility record is outside both image COPY
closures. Before live dispatch, versioned package validation reconstructs the
positive package from current declared sources and the selected immutable YAML/
fixtures, then compares every staged file. It does not require the old broad image
or put the reference back in the public environment.


## Checkout in a Harbor-managed world

Checkout declares a trusted Compose file and Dockerfile appendix through native
`task.toml` metadata (`metadata.sapi.verifier_compose` and
`metadata.sapi.verifier_dockerfile`). These must name explicitly trusted manifest
files. The adapter copies Compose into Harbor's verifier build context and appends
the trusted recipe only to the verifier Dockerfile. The public build is unchanged.
The benchmark installer verifies every upstream file against the unchanged source
pin and the fastjsonschema wheel against its recorded hash. Those external bytes
enter only the trusted image; they are neither vendored nor modified.

Harbor starts a fresh verifier `main` and `simulator` service after stopping the
author. The benchmark's `prepare` hook binds the semantic workflow window and
returns a `RunBinding` containing its tool endpoint, scoped token and absolute
deadline. Core compiles and executes the single observation using the declared
operation bundle. The benchmark's `snapshot` hook freezes receipts, environment
state, native output and its final-answer/Markdown completion facts; its evaluator
scores that recorded evidence independently. No TrialHost begin/finish RPC runs
on this path. Harbor owns both services' startup, teardown and private volume.
The separate network has no published ports; author containers share neither it
nor its credentials volume.

The new checkout trial retains the legacy evidence filenames and hosted acceptance
report schema inside the native verifier output. Explicit `harbor_reward` in the
benchmark verdict projects quality (0.732 for its reference, absent when unscored);
other verdicts retain the acceptance projection. The generic worker knows neither
the checkout challenge nor its completion or scoring rules. Offline re-evaluation
checks the recorded benchmark/options/core closure before loading the selected
evaluator and checks saved task/judge/source identities before scoring. Original
evidence is never rewritten. Legacy hosted paths remain for historical comparison
and unmigrated callers; hard-kill/fault parity is a separate migration gate.


Selected versioned experiments stage native packages for generation, replay and
live execution. Descriptor budgets and live plans provide judge costs and model
occurrences. Checkout declares its unchanged full authoring prompt as public
material; invoice retains its full/scenario catalog arms. Wrapper HTTP transport,
host settings and inspected identity live in `harbor_integration/model_wrapper.py`;
the recorded author-agent import path remains a compatibility facade over the
integration agent. Agency accounting remains in core and receives its transport
explicitly. Integration can import generic execution contracts/mechanisms; core
execution does not import concrete wrapper transport.

The verifier receives a requested judge identity without host credentials or paid
dispatch capability. After native execution, the host evaluates recorded evidence
into a new derived directory under the existing reservation. Checkout's verifier
main has a separate Agency network; the simulator remains on its private internal
network. Versioned re-evaluation checks recorded benchmark/options/core sources and
saved contract/judge identity before the benchmark evaluator can enter the host
reservation callback. Explicit paid/calibration dispatch uses declared judge costs;
offline saved judgement re-evaluation never dispatches. Original evidence and native
Harbor exceptions remain recorded alongside derived paid results.


Generic bounded refinement is compiled with explicit trusted operation source,
including every recursively lowered attempt. `verification/refinement.py`
independently verifies sequential native attempt edges, unchanged deadlines,
carry, stopping, exhaustion and model occurrence identities. Its required
`check_steps` callable supplies independent operation expectations; the mechanism
contains no benchmark rules. The legacy reply evaluator adapts this mechanism
while its retired package awaits removal. Numeric test-only fixtures under
`tests/support/refinement/` exercise these semantics with all production benchmark
directories absent and with an unpaid local bridge in a Harbor-managed native
n8n control. Infrastructure retries are zero and do not implement refinement.

## Explicit lifecycle acceptance

`LifecycleController` requires a trusted acceptance callable before registering or
finalizing a candidate. Its verifier label is recorded data, never a dispatch key.
`observe(..., acceptance=...)` and `lifecycle.main(..., verifier=...)` pass that
callable explicitly; an absent callable fails closed instead of selecting digest
behavior. Existing `sapi-lab lifecycle` and the legacy `observe.main` command
compose their decision through `coordinate/legacy_lifecycle.py` until retirement.
The generic controller and `observe(...)` never select a business callable.

`verification/lifecycle.py` independently checks admitted native event identity,
revision, graph lineage and unchanged deadline. `verify_lifecycle` receives an
explicit independent acceptance callable and retains rebuild, archive, restoration,
scheduling and unknown-reservation checks. Generic submission planning accepts the
Cron instant and deliberately wrong output explicitly. Legacy digest verification
wrappers remain compatibility code until retirement; they are never the generic
controller or audit's default. Test-only lifecycle fixtures provide unrelated JSON
decoding, its own catalog/operation source and independent expected values without
reading a benchmark directory. Native proof uses these fixtures in pinned n8n.


The single frozen legacy import from `coordinate/lifecycle.py` to
`evaluate/operational.py` is relocated to `coordinate/legacy_lifecycle.py`.
The boundary test replaces exactly that edge when comparing against the unchanged
baseline snapshot; the original controller edge is now forbidden. This preserves
existing command behavior during retirement without adding a registry or allowing
any new business imports in generic lifecycle execution.
