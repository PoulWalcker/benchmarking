# Architecture

## Owners and flow

A selected benchmark follows `benchmark -> compile -> execute -> evidence -> evaluate -> result`.
The workflow stages receive a definition, optionally produced by authoring:

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
     |
  AUTHOR (optional)
```

The ownership direction is `BENCHMARKS -> CORE -> external Harbor 0.21.0`.
Benchmarks declare operations, worlds and independent business evaluation. Core
supplies workflow mechanisms and experiment policy; its integration adapts tasks
to Harbor. Harbor manages infrastructure around the flow, without owning workflow
semantics or acceptance. `coordinate/` selects descriptors and sequences the stages.

Each component has an explicit `INPUT -> PROCESS -> OUTPUT` contract:

| Owner | Input | Process | Output |
| --- | --- | --- | --- |
| Benchmark | public task material, fixtures or world observations | declare operations/hooks; independently plan and score | descriptor, observation plan and normalized verdict |
| Compiler | definition, explicit bindings and trusted operation source | validate and lower generic workflow semantics | backend artifact and step map |
| Execute | artifact and explicit run binding | run n8n and record native observations | immutable engine evidence |
| Verifier mechanisms | recorded plan/evidence and independent callbacks | check provenance, obligations and optional rubric | acceptance and separate quality facts |
| Coordinate | selected descriptors, budgets and identities | sequence stages and reserve model calls | durable report and trial references |
| Harbor integration | positive task packages and native settings | invoke pinned external Harbor | authoritative trial directories and phase outcomes |


## Stages

| Stage | Location | Owns | Never |
| --- | --- | --- | --- |
| Shared | `src/sapi_config_lab/*.py` | profile validation, backend contracts, evidence encoding, pinned sources, wrapper stderr audit | import a stage |
| Author | `author/` | model-written or repaired YAML | compile or score it |
| Compile | `compile/` | validated YAML -> n8n JSON + step map | start n8n or decide acceptance |
| Execute | `execute/` | n8n runs, Agency bridge, Harbor/Docker host tools and host configuration | turn engine success into acceptance |
| Evaluate | `evaluate/` | recorded result reading and review export | rerun a workflow |
| Coordinate | `coordinate/` | CLI descriptor selection, runs, ledger, packaging, live gates, lifecycle controller | hold stage logic another stage owns |
| Integration | `harbor_integration/` | native task translation, agent/wrapper adaptation and Harbor invocation | own benchmark worlds or scoring |
| Verifier | `verification/` | independent acceptance and rubric | import compiler, runtime or coordinator code |

`tests/test_boundaries.py` enforces these import edges. Nothing imports `coordinate/`.

## One scenario registry

Each benchmark owns its assets in `tasks/<name>/`. `instruction.md` and
`solution/config.yaml` are the canonical public instruction and reference. Native
`task.toml` owns direct Harbor infrastructure. The existing experiment commands
select the same assets through `scenario.json` and `legacy-task.toml`; they do not
own a second copy of domain code or prompt material.

The versioned manifest declares public/trusted files, reference, bindings, operations,
legacy experiment task configuration, callable entrypoints, dependencies, budgets and
controls. Benchmark-specific world, completion and scoring declarations live in its
opaque `config`. See [Versioned benchmark loading](#versioned-benchmark-loading).
Commands select `Benchmark` descriptors per call and pass them explicitly through
packaging, controls, generation and live execution; there is no import-time default
catalog, provider table or evaluator table.

A fixture evaluator owns its cases, independent business checks, role contract,
output schemas and optional rubric. It supplies an explicit `FixtureEvaluator` to
generic verification planning and scoring. These mechanisms never select business
code or schemas by benchmark name. A world-backed benchmark declares its own
`prepare` and `snapshot` hooks and independent evaluator. Adding a benchmark needs
only its directory and declared dependencies; provenance is data, not dispatch.

## One run mechanism

Every Harbor experiment (`harbor`, `generate`, `live`) runs through `coordinate/runs.py`:

- a new report directory and source manifest; native controls bind checked-in task
  paths to the exact source-verified public/verifier image IDs; generation uses the
  same native directories with task-owned exact prompt composition, while live
  currently retains its staged task packages;
- each phase re-checks sources, image and pinned inputs;
- `Run.harbor` submits versioned packages through `harbor_integration/runner.py` directly into the report's `jobs/` tree. Harbor owns phase limits and resources; partial trial references remain in the report on failure. Selected descriptors supply the trusted entrypoints and budgets;
- `Run.transport` uses the same integration argv builder for its shared-verifier control; Harbor writes directly into durable output and owns its phase limits. Task input snapshots remain separate from the authoritative trial tree;
- `Run.bridge` runs the budgeted Agency bridge;
- the report is always written, cleanup errors included.

Native control selection reads only task.toml policy (defaults, budgets, reward
expectations and catalog arms). It neither loads descriptors nor creates task
directories. A reusable image build record must match the complete current source
manifest and all selected base-image IDs. Run guards recheck images and task/input
bytes before each dispatch and at finalization.

Paid work is reserved in `coordinate/ledger.py` before it starts; an unknown outcome is never released.

## Benchmark worlds and evaluation

The selected benchmark owns world startup, seeding, tool receipts, completion rules
and independent scoring. Harbor starts its declared verifier services; a trusted
`prepare` hook supplies a `RunBinding` and `snapshot` records the world observations.
Harbor owns trial environments and phase limits. The benchmark retains its semantic
workflow deadline and evidence finalization. Checkout's simulator, scorer and
calibration modules live in its declared benchmark package.

`evaluate/records.py` validates one result shape at worker, re-evaluation and read
boundaries: execution, acceptance and optional quality (`null` is not zero). Controls use explicit descriptor controls
and normalized results, with no hosted/verifier taxonomy. Re-evaluation verifies
the recorded source/options identity before calling the selected evaluator.
Native and terminal completion observations remain separate from acceptance; older
records lacking those observations remain readable with null values.

The generic verifier imports only itself. Its explicit fixture supplies independent
checks, contract/output schemas and rubric callbacks. `plan` states which definitions
must run, `coordinate/observe.py` executes them, and `evaluate` checks recorded
evidence against that plan. Benchmark evaluators read evidence and never rerun it.

## Evidence

Execution evidence is written once; evaluation writes separate derived files beside it. `reports/` holds local runs (ignored). `evidence/` holds committed extracts that durable claims cite; they are never rewritten.

Checkout's native simulator mounts Harbor's verifier output and writes immutable
`world/` observations before tool dispatch and after receipts. Its semantic deadline
and explicit snapshot share one terminal decision; finalizer latency does not extend
the workflow window. Forced process termination can leave only earlier observations,
with no final worker record. Snapshot recovery preserves that absence as unknown and
never constructs missing environment evidence. These observations do not implement
container cleanup or automatic retries; those remain Harbor responsibilities.

Pinned inputs are explicit and fail closed: benchmark-declared upstream source
manifests, profile/specification pins under `provenance/`, engine and Harbor versions,
the image identity, the complete run source manifest, prompt hashes and the inspected
model-wrapper identity. Source guards include documentation; sources stay frozen
through controls, while progress and derived observations go under `reports/`.

## Backend boundary

`N8nBackend(operation_source)` and compiler entrypoints require explicit trusted
JavaScript; execution, bridge and rebuilder callers supply bindings explicitly.
Detached candidates never inherit an invoice catalog or operation bundle.

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
| hosted | historical terminology for world-backed trials; current worlds are declared by benchmark hooks and native Harbor configuration |

## Ownership boundaries

Selected descriptors and explicit callbacks supply benchmark behavior; there is
no shared business catalog or provider/evaluator dispatch table. Independent
checks receive recorded evidence and never import the implementation under test.

`tests/test_boundaries.py` enforces structural ownership without migration allowlists.
Every generic Python module and package initializer has a stage. Task-owned
`evaluation/` and `environment/` Python modules may import neutral contracts and independent verification, but not coordination or compiler/runtime
implementations. Core and independent verification cannot import task domain
packages; only `harbor_integration` imports Harbor. Independent verification imports only its own mechanisms. Static absolute,
relative and literal dynamic imports are checked, including function-level imports.
The selected `benchmark_loading` seam loads declared trusted modules; resource access
does not authorize dynamic code imports. These checks do not detect arbitrary computed
imports, business logic embedded in JavaScript, file reads or name-based dispatch.
The sealed baseline remains evidence, never an exemption from current ownership.

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
current re-evaluation enforces the recorded identity.

`coordinate/benchmark_discovery.py` resolves selections from an explicit search
root without global tables or caches. Only invoice-total (default) and
checkout-recovery remain production-discoverable, both through positive packaging.
Test-only graph specimens have no production manifests or runtime selection.
Historical reads do not require retired benchmark directories.

## Positive Harbor packages

`harbor_integration/tasks.py` positively packages selected versioned benchmarks.
It validates the descriptor-selected `legacy-task.toml` against pinned Harbor 0.21.0.
Integration modules may import neutral contracts, generic execution mechanisms and each other;
benchmark planning and scoring stay outside.
All active benchmark task packaging uses the versioned path.

Repository `tests/` contains project unit and regression tests. A benchmark's
source `evaluation/` and `verifier.sh` supply its task-specific verifier. Staging
emits Harbor's standard task layout: `tests/test.sh` runs that verifier with its
private `tests/payload` and generic `tests/core`, while `solution/solve.sh` runs
the reference solution.

`stage_benchmark` copies declared public files into `environment/payload`, public
and trusted files into `tests/payload`, and the reference into `solution/`. Public
`instruction.md` is copied verbatim to the task root. Shared dependencies retain
`dependencies/<alias>/` paths in each payload. Generated build recipes start from
`harbor_integration/runtime/Dockerfile`: only pinned upstream Alpine/n8n runtime
bytes enter its ancestors. No legacy image, checkout or private file enters the
public image and is later scrubbed. `inputs.json` hashes the source/options identity
and every staged file.

The supported profile is single-step Linux/Docker with an unprivileged author (UID
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
are outside this profile. Its Docker limits are described under [Timeouts and resources](#timeouts-and-resources).

Harbor builds the verifier from `tests/` and skips subsequent test upload. The image
includes `/tests/test.sh`, its declared payload and dependencies. Build-time isolated
imports reject unresolved entrypoint imports before verification starts. No reference
enters the verifier image. Packaging does not invoke jobs or implement the benchmark's
compile/execute/evaluate pipeline.

### Invoice package execution

`tasks/invoice-total` owns its unchanged public catalog, trusted JavaScript
operation bundle as explicit compatibility material, case/probe planning,
independent totals, role contract/output schemas,
and deterministic rubric. Its explicit fixture object supplies these to verifier
mechanisms. Keeping the full bundle preserves execution of every operation its unchanged catalog exposes,
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
bundle, and calls the local evaluator on frozen evidence. It writes the authoritative
normalized `result.json` beside any benchmark-specific verification report. Preflight uses
compilation-only admission only for benchmarks declaring a `prepare` hook; fixture
benchmarks still run their observation plan and independent evaluation. Admission
leaves execution, acceptance and quality null. Harbor keeps ownership
of the two environments, collection, transfer and phase limits. Missing YAML yields
rejected acceptance, null execution/quality and deterministic reward zero.

Active callers receive explicit selected descriptors. Benchmark-owned `config`
is interpreted only by its declared hooks or by command composition for public
prompt material; it does not recreate a provider/evaluator registry.

Generation/selection retain the host-side `tests/cases.json` record and
its historical hash format. This compatibility record is outside both image COPY
closures. Before live dispatch, versioned package validation reconstructs the
positive package from current declared sources and the selected immutable YAML/
fixtures, then compares every staged file. It does not require the old broad image
or put the reference back in the public environment.


## Checkout in a Harbor-managed world

Checkout declares a trusted Compose file and Dockerfile appendix through
descriptor-selected `legacy-task.toml` metadata (`metadata.sapi.verifier_compose` and
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
scores that recorded evidence independently. Harbor owns both services' startup,
teardown and private volume.
The separate network has no published ports; author containers share neither it
nor its credentials volume.

Checkout retains the recorded evidence filenames and hosted acceptance
report schema inside the native verifier output. Explicit `harbor_reward` in the
benchmark verdict projects quality (0.732 for its reference, absent when unscored);
other verdicts retain the acceptance projection. The generic worker knows neither
the checkout challenge nor its completion or scoring rules. Offline re-evaluation
checks the recorded benchmark/options/core closure before loading the selected
evaluator and checks saved task/judge/source identities before scoring. Original
evidence is never rewritten. Historical hosted records remain available through
snapshot readers using verified original evaluator bytes, without an import shim
to a trial server or current business scorer.


Live versioned experiments currently stage native packages for replay and
live execution. Descriptor budgets and live plans provide judge costs and model
occurrences. Checkout declares its unchanged full authoring prompt as public
material; invoice retains its full/scenario catalog arms. Wrapper HTTP transport,
host settings and inspected identity live in `harbor_integration/model_wrapper.py`;
new runs name the integration-owned YAML agent directly. Recorded historical agent
paths remain immutable report data. Agency accounting remains in core and receives its transport
explicitly. Integration can import generic execution contracts/mechanisms; core
execution does not import concrete wrapper transport.

## Installed resources

Wheel resources contain the native task directories, their manifests and complete
public/trusted/reference and shared dependency closure, unchanged generation prompts,
and the Docker recipes for `infra/native/build.sh`. Explicit Docker build contexts
supply core and verification from the checkout or their installed Python packages.
The recipes build from an installed resource root without a task export step.
The build hook resolves those declarations without importing benchmark implementation.
Resource discovery, compilation and task staging work from a clean installation.
Legacy staging copies generic runtime and independent verifier sources from the actual
installed packages; it never reconstructs an editable source tree. Public/trusted
placement rules apply equally to installed and checkout resources.

Experiment controls, live model dispatch and source/image identity checks still
require the matching editable workspace. Installed task packages can be handed to
pinned Harbor directly; installing resources does not authorize a paid experiment.
Distribution checks install both a direct wheel and a wheel built from the source
distribution in isolated environments, then inspect invoice, checkout and a declared
shared dependency outside the checkout. Benchmark Python participates in lint and
separate type checks for each manifest-owned namespace.

The verifier receives a requested judge identity without host credentials or paid
dispatch capability. After native execution, the host evaluates recorded evidence
into a new derived directory under the existing reservation. Checkout's verifier
main has a separate Agency network; the simulator remains on its private internal
network. Versioned re-evaluation checks recorded benchmark/options/core sources and
saved contract/judge identity before the benchmark evaluator can enter the host
reservation callback. Explicit paid/calibration dispatch uses declared judge costs;
offline saved judgement re-evaluation never dispatches. Original evidence and native
Harbor exceptions remain recorded alongside derived paid results.


## Bounded refinement

Generic bounded refinement is compiled with explicit trusted operation source,
including every recursively lowered attempt. `verification/refinement.py`
independently verifies sequential native attempt edges, unchanged deadlines,
carry, stopping, exhaustion and model occurrence identities. Its required
`check_steps` callable supplies independent operation expectations; the mechanism
contains no benchmark rules. Numeric test-only fixtures under
`tests/support/refinement/` exercise these semantics with all production benchmark
directories absent and with an unpaid local bridge in a Harbor-managed native
n8n control. Infrastructure retries are zero and do not implement refinement.

## Timeouts and resources

| Owner | Limit | Meaning |
| --- | --- | --- |
| Native `task.toml` / Harbor | build, author, collection and verifier phases; CPU and memory | trial infrastructure limits, with zero automatic retries |
| Core admission | planned execution ceilings against the resolved verifier phase | reject oversized plans before dispatch |
| Core n8n executor | import/process ceilings and remaining absolute run deadline | bound engine processes without adding a whole-job watchdog |
| Benchmark world | semantic window established by `prepare`; one terminal snapshot | finalizer latency does not extend workflow time |
| Agency / ledger | grant expiry, occurrence budgets and reservations | bound and account for model work; a grant duration is not an infrastructure watchdog |
| Benchmark evaluator | pinned worker/judge limits | bound evaluation of recorded evidence |

Trial infrastructure supports the pinned single-step Linux/Docker profile. Docker
enforces CPU and memory;
packaging rejects requested storage, GPU and TPU requirements. Separate author/verifier
placement, private Compose networks, sidecar credentials and durable observations
have Docker-specific controls. This does not establish storage/network enforcement
or log-retention equivalence on other Harbor providers.
Interrupted runs retain only observations actually written and available; missing
native or terminal facts stay unknown.

## Explicit lifecycle acceptance

`LifecycleController` requires a trusted acceptance callable before registering or
finalizing a candidate. Its verifier label is recorded data, never a dispatch key.
`observe(..., acceptance=...)` and `LifecycleController(..., verifier=...)` receive
that callable explicitly along with trusted bindings/backend composition. There is
no CLI-selected business decision or default acceptance callback; lifecycle callers
must inject the trusted callable through Python.

`verification/lifecycle.py` independently checks admitted native event identity,
revision, graph lineage and unchanged deadline. `verify_lifecycle` receives an
explicit independent acceptance callable and retains rebuild, archive, restoration,
scheduling and unknown-reservation checks. Generic submission planning accepts the
Cron instant and deliberately wrong output explicitly. Test-only lifecycle fixtures
provide unrelated JSON decoding, their own catalog/operation source and independent
expected values without reading a benchmark directory. Native proof uses these
fixtures in pinned n8n.

## Historical records

`evaluate/records.py` reads trial results and historical report layouts without
importing execution, provider or lifecycle tables. New versioned trials require the
recorded normalized verdict; missing verdicts remain unevaluated and malformed
verdicts fail without a legacy fallback. Legacy report reconstruction applies only
to historical trials. The saved execution, acceptance, quality and reward remain separate facts; missing native and terminal observations
remain null. Review export resolves absolute historical references through an
explicit relocated run root without rewriting recorded paths or rewards.

Historical re-evaluation uses `coordinate/historical_evaluation.py` with an explicit
trusted source checkout and its frozen source manifest. It checks independent
evaluator bytes and recorded source/contract identity before loading snapshot code.
The historical hosted contract layout names its original scorer as snapshot data. Fixture snapshots additionally match the recorded aggregate
verifier identity. Re-evaluation is offline and writes a separate derived output;
missing snapshots refuse precisely while ordinary reads remain available. Versioned
records continue to use their declared evaluator and complete frozen identity.

The [sealed baseline index](../evidence/migration-01-baseline/INDEX.md) records original
source/prompt identities and historical formats. Archived scorer fixtures retain their
verified original path and hash; a current benchmark scorer cannot substitute for
that original evaluator.

## Native Harbor integration (Phase 1)

`tasks/invoice-total` and `tasks/checkout-recovery` are also checked-in Harbor task
packages. Harbor consumes them directly; there is no descriptor discovery or task
materialization on this path. Their trusted Python entrypoints import the task-owned
planners, world hooks and evaluators explicitly, then call
`coordinate.benchmark_worker.run_task`, the same observation/evaluation composition
used by the legacy worker. These trusted scripts are native coordination roots:
`main.py` for each task, plus checkout's `fake_bridge.py` and
`calibration_transport.py`. They may import core stages and only their explicitly
listed trusted `payload` entrypoints; core and verification never import them.
`tests/test_boundaries.py` enumerates every task Python file, requires an explicit
owner and rejects undeclared benchmark imports, Harbor imports and reverse edges.
This adds native composition roots while preserving lower-stage and independent
evaluator boundaries. The legacy manifest path remains supported.

`infra/native/build.sh` builds explicit image targets from the checkout. Public
stages copy only original public material and the existing submission gate from a
pinned Alpine ancestor. Trusted stages copy the existing generic runtime and the
selected task's trusted source files; references enter only Harbor's oracle
solution directory. Checkout's private Compose network and credentials volume are
owned by Harbor. The author environment explicitly uses Harbor `no-network`;
the separate verifier environment uses its own `public` policy and private
Compose verification network. The simulator has no published port or author
network/credential mount. Native unpaid runtime controls use a loopback fake
transport with the existing Agency reservation/failure mechanisms.

Checkout's native judge input is the immutable saved simulation reply from the
pinned spike. `calibration_transport.py` verifies its SHA and scorecard/prompt
identity, projects only its fixed answer values into a new explicitly simulated
reply bound to fresh recorded evidence, and records original provenance and the
adapter request identity in `calibration-transport.json`. It never mutates or
replays the historical reply against a different run. The original scorer still
rejects mismatched evidence digests. No model judges the fresh prose; missing
required evidence remains unscored. Neither native transport exposes paid calls.
Each trusted task image freezes SHA-256 hashes of its complete `/tests` tree,
checks them before verification and records that inventory beside evidence. Native
control tests also record host sources and base image IDs. Native task sources and
explicit image build inputs are also installed as package resources. They require rebuilt local images; experiment identity gates continue to
require a matching editable checkout. Installed legacy staging remains available. See
[NATIVE_PARITY.md](../NATIVE_PARITY.md) for verified coverage and Phase 2 scope.

Generation invokes each selected native task's fixed `experiment.py` in a source-checked
Python subprocess with isolated imports and no bytecode writes. That task composes its
original prompt arms; the host pins the resulting prompt and private fixture record.
The upload-only wrapper agent receives an explicit prompt path/hash, reserves one
authoring call before dispatch, and uploads its answer unchanged. Checkout compilation
admission is generation eligibility with null evaluated facts; invoice eligibility
requires independent fixture acceptance. Selection remains the first-started attempt.
The upload-only replay agent verifies selected bytes and makes zero generation calls.
