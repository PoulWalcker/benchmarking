# Development

## Setup

```bash
uv sync --locked --extra harbor --extra benchmark
uv run --locked sapi-lab fetch-source autowfbench   # host cache for offline scoring/source-backed tests
```

Python is pinned to `3.14.8` by `.python-version`; `--locked` keeps `uv.lock` authoritative. Docker is needed for the n8n/Harbor controls; a local n8n container only for `sapi-lab ui`.

`fetch-source` resolves the trusted pin declared by a benchmark (checkout declares
`benchmarks/10-checkout-recovery/provenance/autowfbench-source.json`) and verifies
every byte under `.cache/<source>/<revision>/`. A changed cache is refused rather
than repaired. Native checkout verifier builds independently fetch their declared
private source/wheel dependencies; the host cache supports offline scoring and
source-backed tests.

## Checks

```bash
uv run --locked sapi-lab check
```

It runs the unit tests, `ruff check`, `ruff format --check`, `mypy` and `infra/check_distribution.py`, prints each stage to stderr and exits 1 naming every stage that failed. These deterministic checks include clean distribution builds/installations but
do not execute real n8n. For engine evidence run the unpaid control suite:

```bash
./run.sh                          # invoice-total, the sole default
./run.sh --scenario invoice-total --scenario checkout-recovery
```

A control is valid only when `oracle` passes and `nop` fails. Fix the instrument before spending model calls. Hosted nop permits only Harbor’s expected `RewardFileNotFoundError` when quality is unscored and no reward exists; a deterministic evaluator with null quality must record reward zero without an exception. Every control job must exit successfully.

## CLI

`uv run --locked sapi-lab --help` is the source of truth for options.

| Command | Purpose | Model calls |
| --- | --- | --- |
| `check` | the required local checks | no |
| `compile` / `build` | validate and compile one config / every reference config | no |
| `harbor` | the control suite (`./run.sh`) | no |
| `generate` | model-authored YAML after a passing control suite (`./run-generation.sh`) | yes |
| `select` | pick generated submissions for replay by a fixed rule | no |
| `live` | replay submissions with live model operations | yes |
| `evaluate` | re-evaluate one recorded trial; a judge only with `--dispatch-judge` | only if dispatched |
| `ui` | import graphs into local n8n; a `--live` session may call models | sometimes |
| `review-export` | write derived `analysis.md` in Harbor trials, discovering authoritative hosted reports from the run report; hosted rewards remain recorded facts | no |
| `fetch-source` | fetch and verify a pinned upstream source | no |

`package-tasks <destination> --scenario <name>` stages a new task directory without
running it, including from a clean supported installation. Internal execution,
transport and bridge commands need their runtime environment. `lifecycle` inspects
state; execution uses the explicit Python acceptance/backend composition described
in [ARCHITECTURE.md](ARCHITECTURE.md#explicit-lifecycle-acceptance).

## Machine configuration

Defaults describe one Docker Desktop host. Override them through the environment; a CLI option, where one exists, overrides the environment.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SAPI_LAB_ROOT` | discovered from the working directory | the checkout experiments read |
| `SAPI_WRAPPER_URL` | `http://127.0.0.1:8765/run` | local model wrapper (`--upstream`) |
| `SAPI_WRAPPER_MODEL` | `gpt-6-astra` | model the wrapper must report; a mismatch fails closed |
| `SAPI_CONTAINER_HOST` | `host.docker.internal` | how a task container reaches those services (e.g. `172.17.0.1` on Linux) |
| `SAPI_LISTEN_HOST` | `127.0.0.1` | bind address of host services containers reach: Agency bridges (`ui --host`); e.g. `0.0.0.0` on Linux |
| `SAPI_BRIDGE_PORT` | `18765` | live Agency bridge (`live --bridge-port`) |
| `SAPI_UI_BRIDGE_PORT` | `18766` | UI Agency bridge (`ui --port`) |
| `SAPI_N8N_URL` | `http://localhost:5678` | local n8n editor |
| `SAPI_N8N_CONTAINER` | `n8n-n8n-1` | local n8n container (`ui --container`) |
| `SAPI_STAGING_DIR` | system temp | staging for task packages; must be mountable by Docker |

The coordinator sets the container protocol variables itself: `SAPI_LLM_MODE`, `SAPI_CASE_NAME`, `SAPI_BRIDGE_URL` (Harbor verifier env) and `SAPI_EXPECTED_SUBMISSION_SHA256` (replay packages). n8n inherits only `PATH`, `HOME`, `TMPDIR`, `TZ`, `LANG` and `LC_ALL` plus a run's binding.

## Adding a scenario

Add `benchmarks/NN-<name>/` with a `scenario.json` (fields in [ARCHITECTURE.md](ARCHITECTURE.md#one-scenario-registry)), a reference `config.yaml` that obeys [PROFILE.md](PROFILE.md) and `generation/FORMAT.md`, and an `instruction.md`.

Declare its public/trusted files, bindings, operation JavaScript, native `task.toml`,
reference and trusted `plan`/`evaluate` entrypoints as described under
[Versioned manifest development](#versioned-manifest-development). A fixture
benchmark owns evaluator-only cases, contract/output schemas, independent business
checks and optional rubric. Its evaluator passes an explicit `FixtureEvaluator` to
generic verification; no shared registration or benchmark-name dispatch is needed.
Checks recompute expected values from fixtures without importing runtime operations.

A world-backed benchmark owns its world and declares `prepare`/`snapshot` hooks,
trusted native environment files and its independent evaluator inside the directory.
Completion/output rules are benchmark-specific. There is no provider/evaluator table
to extend. Public material enters the author payload; evaluator sources and private
fixtures enter only the separate verifier payload. The reference enters only the
oracle solution.

The declared bindings and trusted operation source are passed explicitly through
compilation and execution. Native `task.toml` owns phase limits and resources;
admission checks the planned execution budget against the resolved verifier phase.
Fixture planning rejects a deadline above its admitted budget. World-backed model
admission compiles without starting a world, checks runtime model calls and keeps
the benchmark's declared deadline. Harbor owns infrastructure startup, teardown and
retries; workflow deadlines remain separate.

Cover profile validity, packaging, independent positive acceptance and rejection of
a plausible corrupted result, then run `./run.sh --scenario <name>`. Do not add a
shared business registry or a docs file per scenario.

### Directory-only extension

Use `tests/support/extensibility/` as the complete example of an unrelated
operation/world/evaluator contract. Its beacon operations, private world seal,
completion rules and independent expectations live in that directory. A new
benchmark follows the same procedure:

1. Add its directory and explicit v1 manifest, including public material, trusted
   operations/helpers, a separate reference and any declared `_shared` dependency.
2. Add bindings and operation implementation locally. Pass both explicitly into
   generic compilation; the candidate cannot choose an executable source path.
3. Implement independent `plan`/`evaluate` hooks. A world-backed task also declares
   its own `prepare`/`snapshot`, private environment recipe and native task settings.
4. Cover positive acceptance, nop rejection, a plausible corrupted result and
   immutable offline re-evaluation, then stage and run the named benchmark:

```bash
uv run --locked sapi-lab benchmarks
uv run --locked --extra harbor --extra benchmark sapi-lab package-tasks /tmp/new-task --scenario <name>
./run.sh --scenario <name>
```

Use a destination that does not exist. Only the new directory and its declared
dependencies are needed; core modules, shared business tables and existing benchmark
bytes stay unchanged. Expected values are recomputed from fixtures/world evidence,
not imported from the operation implementation.

The existing opt-in proof performs the whole temporary extension automatically:

```bash
SAPI_RUN_DOCKER_TESTS=1 uv run --locked --extra harbor --extra benchmark \
  python -m unittest tests.test_benchmark_extensibility -v
```

It temporarily copies the fixture to `benchmarks/99-beacon-calibration`, discovers
and stages it through the public CLI, then runs fresh Harbor oracle/nop worlds.
It rejects corrupted completion, re-evaluates without changing original evidence,
checks unchanged existing source hashes and removes the temporary directory.
Do not pre-create that directory when invoking the proof. Its native artifacts are
written under `reports/migration-20/extension-*`.

## Model-authored definitions

`./run-generation.sh` gives a model the task, `generation/FORMAT.md`, `generation/PROFILE.md` and the operation catalog, once per attempt, with no repair. `--catalog scenario` is an experiment arm, not the default: the catalog shows only the operations the scenario's reference uses, the report records the variant, the operations shown and their hashes, and its prompts are pinned separately. `sapi-lab package-tasks --mode generation --catalog scenario` stages those prompts without a model call. The answer is a candidate like any other: compiled, executed and independently verified. Runtime model steps are stubs during generation. The wrapper does not disable its CLI tools: the prompt forbids them and recognized tool markers in its stderr reject the attempt, which is an audit, not a sandbox. `tests/test_packaging.py` pins every generation prompt's hash; a prompt change is an experiment change.

For a hosted scenario the gate after authoring and before live dispatch is admission: the YAML compiles in live mode, stays within `runtime_model_calls` and keeps the benchmark's declared deadline. It proves materialization capability and declared limits, not candidate execution or acceptance. Hosted model operations need no fabricated stub answer. Fresh oracle/nop reference executions still gate the instrument before paid dispatch.

## Live model execution

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab live --stub-report <control>/report.json \
  --max-calls N --wrapper-evidence <identity.json> [--submissions-manifest <selection.json>]
```

Before any dispatch, `live` requires a passing control report for the same sources and image, unpaid fixture stub replay or hosted compilation/admission, a ledger reservation per case and an inspected wrapper identity. The identity records each wrapper file's hash; files are read at their recorded path unless `--wrapper-file NAME=PATH` names the local copy (NAME is the file's basename). Hashes are always checked. A hosted evaluator that calls a judge also needs `--judge-model`; its `judge_calls` are reserved per trial.

`sapi-lab evaluate --record <trial>/verifier --output <new-derived-directory>`
re-evaluates a native trial through its recorded benchmark evaluator. For checkout,
`--judgement <reply.json>` re-scores a saved judgement without a model call.

## Local n8n UI

```bash
uv run --locked sapi-lab ui open --all
uv run --locked sapi-lab ui open benchmarks/01-invoice-total/config.yaml --live
```

Imports are inactive copies and never execute. A `--live` session arms one fresh copy with a budgeted bridge; execution stays a manual click. UI runs are for inspection, not benchmark evidence.

Compile and UI commands resolve a benchmark's declared bindings and trusted
operation source when given its reference path. For a detached candidate, select
`--scenario invoice-total` (or another benchmark). With no benchmark context,
detached compilation requires both trusted files explicitly:

```bash
uv run --locked sapi-lab compile candidate.yaml --output compiled.json \
  --bindings /trusted/bindings.yaml --operations /trusted/operations.js
```

There is no installed global catalog, fixed operation bundle or detached fallback.
Explicit file composition works without an experiment checkout; descriptor selection
uses this installation's declared resources. Candidate YAML never selects executable paths.
UI imports require local operations; remote tools require a Harbor trial.
`package-tasks`, controls, generation and live execution receive selected descriptors
explicitly. Generic lifecycle APIs require an injected trusted acceptance callable,
bindings and backend; the CLI does not supply a default business decision.

## Reading a run

| Question | Evidence |
| --- | --- |
| Did the engine run? | `case.json` and the native n8n records beside it |
| Did the required behavior hold? | the verifier's `report.json` and each case's `acceptance.json`, or the hosted evaluator's `evaluation/report.json` |
| How good was the output? | `evaluation.json`; `not_evaluated` is missing, not zero |

New runs go to `reports/` (ignored). Commit to `evidence/` only what a durable claim cites, and never edit it afterwards.

## Documentation

One fact, one owner: boundaries in `ARCHITECTURE.md`, workflow here, YAML semantics in `PROFILE.md`, the entry path in `README.md`, coding rules in `AGENTS.md`. History lives in Git and `evidence/`, not in `docs/`.

## Versioned manifest development

`uv run --locked sapi-lab benchmarks [--root <benchmark-directory>] [--defaults]`
lists metadata without importing evaluators.
Only invoice-total and checkout-recovery are production benchmarks, both versioned.
Invoice-total is the sole default; retired names are unknown selections.
Existing command names and default selection are preserved. `package-tasks` and
the unpaid `harbor` controls materialize both through their declared entrypoints
and separate verifiers. Experiment consumers receive explicit descriptor selections.

The `sapi-lab-benchmark/v1` manifest has these required keys. Unknown keys fail;
benchmark-specific declarations belong inside `config`.

| Key | Representation |
| --- | --- |
| `version`, `id`, `default` | Literal `sapi-lab-benchmark/v1`, lowercase CLI name, explicit boolean |
| `public`, `trusted` | Lists of existing relative file paths; no directory/glob expansion |
| `reference` | One separate trusted oracle file, never also public/trusted |
| `bindings`, `operations`, `harbor_task` | Destinations already declared public/trusted; `harbor_task` names a native `.toml` file |
| `entrypoints` | Required `plan`, `evaluate`, optional `prepare`, `snapshot`; each `{ "path": "evaluation/evaluator.py", "symbol": "evaluate" }` names a declared trusted Python file and one synchronous callable |
| `dependencies` | Object keyed by Python identifier alias; each `{ "path": "_shared/family", "public": [], "trusted": ["rules.py"] }` lists explicit files under the search root; destinations/imports use `dependencies/<alias>/...` |
| `controls` | `{ "oracle_acceptance": true, "reference_reward": null }`; oracle acceptance must be literal `true`; reward is null or a finite number in 0..1 |
| `budgets` | `{ "authoring_attempts": null, "runtime_model_calls": null, "judge_calls": 0 }`; nonnegative integers, null permitted only for the first two (no benchmark-specific ceiling); zero refuses that work |
| `config` | Opaque JSON object, including benchmark-owned cases, output, completion or provenance declarations |

An empty dependency object is valid. All paths reject absolute/parent traversal and
symlinks; duplicate destinations, classifications and Python module identities fail.
`scenario.json` and the root `dependencies/` destination are reserved. Entrypoints
may import their declared sibling helpers relatively; shared helpers are reached
through the package's `dependencies.<alias>` namespace. Absolute installed-library
imports remain governed by the environment's pinned dependencies. Manifest hashes
are computed from bytes at staging, not embedded recursively in the manifest.

Use `load_benchmark(root, directory)`, then `freeze_identity(descriptor, options)`
and `load_entrypoints(descriptor, identity)` only in a trusted context. Listing is
separate from selection and never calls these hooks. The manifest suite models
both invoice-style plan/evaluate and checkout-style prepare/snapshot needs without
changing existing benchmark files. The versioned packaging path below adds
positive container contexts and pinned native Harbor configuration validation.

### Versioned task packaging

Versioned manifests can be staged with
`harbor_integration.tasks.stage_benchmark(descriptor, destination, options)`.
The destination must not exist. Declare `instruction.md` as public, `verifier.sh`
as trusted, and the native task configuration as trusted. The shell entrypoint
runs from `/tests/payload` with recorded output under `/logs/verifier`; its verdict
and reward remain benchmark-owned. Local evaluator imports are checked under the
`payload` namespace while building the verifier. Declared shared modules are at
`payload.dependencies.<alias>`. The base runtime supplies Python and PyYAML. Benchmark-specific external
dependencies belong to its trusted native build recipe and source/wheel pins; local
Python helpers belong to the declared manifest closure.

The current YAML transfer profile requires these native Harbor settings (resource
limits and timeouts otherwise remain native configuration):

```toml
artifacts = [
  {source="/logs/artifacts", destination="discarded-convention", exclude=["*"]},
  {source="/submission/config.yaml", destination="submission/config.yaml"}
]
[agent]
user = "1000"
[verifier]
environment_mode = "separate"
[[verifier.collect]]
command = "python3 /opt/sapi-submission.py collect"
user = "root"
timeout_sec = 10
[verifier.environment]
cpus = 1
memory_mb = 512
```

Author submissions remain at `/app/submission/config.yaml`; verification reads
the admitted snapshot at `/submission/config.yaml`. Only a regular non-executable
YAML mapping of at most 1 MiB is admitted, with no other files in the submission
directory. This does not replace workflow-schema validation. The separate verifier
must be declared explicitly without `docker_image`, otherwise Harbor may inherit
a public prebuilt image and bypass the trusted Dockerfile. Packaging rejects that
configuration. Do not add runtime job overrides that weaken the validated user,
artifact, mount, environment or collection settings.

The real packaging controls are opt-in and unpaid:

```bash
SAPI_RUN_DOCKER_TESTS=1 uv run --locked --extra harbor --extra benchmark \
  python -m unittest tests.test_benchmark_packages -v
```

They retain inspection results and raw image/filesystem archives under
`reports/migration-03/docker-*`, inspect every public image layer and native mounts,
and remove only their own retained containers and networks. Normal unit tests skip
this Docker proof. Invoice-total additionally exercises this path through `./run.sh --scenario invoice-total`.

Invoice task packages retain the original `instruction.md` bytes for controls. Its
historical reference-path wording is unchanged; the reference now enters only via
Harbor's oracle solution. Model authoring uses the separately pinned generation
prompt, with both full and scenario catalog bytes unchanged. The verifier runs the
same 14 observations (three positive, seven invalid-input, four corruption/schema
probes). `tests/benchmark.json` records the deadline admission budget, source closure
and frozen staging options; evidence and derived evaluation keep their old layout.


Checkout's native suite covers normal oracle/nop, private placement and the failure/
isolation matrix. It is opt-in and unpaid:

```bash
SAPI_RUN_DOCKER_TESTS=1 uv run --locked --extra harbor --extra benchmark \
  python -m unittest tests.test_harbor_checkout -v
./run.sh --scenario checkout-recovery
```

The proof retains oracle/nop results, phase placement, fresh-world observations,
artifact bytes and public image inspection under `reports/migration-05/`. The
trusted image build needs network access to fetch hash-pinned upstream sources and
its pinned Python dependency. Runtime world traffic stays on the private Compose
network. Control runs use the local demo judge and make no paid calls.

For the versioned path, pass the trial's native `<trial>/verifier` directory to
`sapi-lab evaluate --record ... --output ... --judgement ...`. It includes
`benchmark.json`, `evidence/` and the saved `evaluation/task-contract.json`.
Re-evaluation requires the recorded benchmark/core source identity and verified
local upstream cache. A source mismatch fails before scoring. Native phase limits,
workflow deadlines, null quality and legacy historical readers retain their
separate meanings.


Versioned control and authoring jobs write directly into `reports/<run>/jobs/`.
`harbor_jobs` records each job's relative path and available trial/evidence paths,
including interrupted trials without a final native result. `review-export` can
discover native verifier reports directly after partial failure or moving a run.
[Architecture timeout ownership](ARCHITECTURE.md#timeouts-and-resources) separates
native Harbor phases, semantic workflow windows, engine ceilings and model grants.
Admission checks plans against resolved verifier limits before dispatch. The
supported resource/privacy controls apply to the pinned Docker profile.

Generation, replay and live use the selected versioned package. Native live staging
freezes `--judge-model` in its source/options identity. The verifier records evidence
and an unscored paid-judge contract; the host evaluates it under the declared judge
reservation into `verifier/paid-evaluation/`, preserving original evidence and native
Harbor results. Checkout's verifier main can reach the scoped host Agency bridge;
its simulator remains on the private verification network.

Versioned `evaluate --dispatch-judge` and `--calibration CASE --judge-model MODEL`
use the recorded evaluator after complete source and saved-contract reconciliation.
A paid judge enters the ledger reservation only after those checks. Saved judgement
replay remains offline. Wrapper transport and inspected local-file identity live in
`harbor_integration/model_wrapper.py`; supported `SAPI_*` settings and CLI forms
are unchanged.


The generic refinement control uses test-only numeric operations and a local
unpaid bridge; it does not discover or run a production benchmark:

```bash
uv run --locked python -m unittest tests.test_refinement tests.test_refinement_admission tests.test_refinement_verification tests.test_occurrence_budget -v
SAPI_RUN_DOCKER_TESTS=1 uv run --locked --extra harbor --extra benchmark python -m unittest tests.test_refinement_verification -v
```

The opt-in native case runs first acceptance, later acceptance, exhaustion and
stub accounting within one Harbor 0.21.0 trial with zero infrastructure retries.
Its source identity, exact command, native engine records, bridge requests and
independent verdicts remain under `reports/migration-11/native-refinement-*/`.

Generic lifecycle native proof is opt-in and unpaid:

```bash
SAPI_RUN_DOCKER_TESTS=1 uv run --locked --extra harbor --extra benchmark \
  python -m unittest tests.test_lifecycle_verification -v
```

It uses the existing pinned n8n lab image and records native executions, registry
snapshots and independent decisions under `reports/migration-12/native-*`.
The controller and observation API require an explicitly injected trusted decision.
The legacy lifecycle adapter has been removed; callers inject their trusted
acceptance callable through Python. There is no CLI or YAML option for selecting
executable acceptance code.

The source distribution and wheel include each descriptor's declared file/dependency
closure, including benchmark-owned source pins. Wheels include generic runtime and
independent verification packages plus manifest-owned resources and unchanged
generation prompts. The distribution check installs direct and source-built wheels
into isolated environments, invokes discovery/compilation/staging from outside the
checkout and checks declared shared dependencies and public/trusted placement.

A clean installation of the built distribution with the `harbor` and `benchmark`
extras supports `sapi-lab benchmarks`,
`compile --scenario <name>`, `build` and `package-tasks <destination> --scenario <name>`
using installed resources. The resulting native task directory can be supplied to
Harbor 0.21.0. Controls, generation/live experiments and source/image identity gates
require the matching editable checkout, as enforced by `workspace_root()`; resource
staging alone does not bypass those gates. Historical re-evaluation requires explicit
verified original source snapshots, including their original source pins.

Ruff checks benchmark Python alongside generic source. `infra/check_types.py` checks
generic packages together and each discovered benchmark separately because their
local evaluator module names can coincide. Only external Harbor/upstream modules
without type metadata are exempt from dependency analysis.


Historical reports can be read through `evaluate.records.read_report(path, root=run_root)`
and exported with `sapi-lab review-export <relocated-run>/jobs --dry-run`. Retired
benchmark names in these records do not require runnable benchmark metadata.

To re-evaluate a historical fixture or hosted record, supply the trusted snapshot
and the source manifest frozen when it was recorded:

```bash
uv run --locked sapi-lab evaluate --record <old-record> --output <new-derived-directory> \
  --source-root <historical-checkout> --source-manifest <frozen-source-manifest.json>
```

Add `--cases <recorded-cases.json>` for expanded fixture inputs and `--judgement
<saved-judge-reply.json>` for a saved hosted judgement. Historical evaluation never
dispatches a judge or starts a world. Unavailable or changed independent evaluator
sources refuse re-evaluation; report inspection remains available. A matching
versioned record uses its recorded descriptor identity and does not need these
historical snapshot options.

## Checked-in native Harbor tasks

The Phase 1 native tasks use the same domain implementations as the existing
manifest path. Build their explicit public and trusted images from this checkout,
then pass the static directories directly to Harbor 0.21.0:

```bash
uv sync --locked --extra harbor --extra benchmark
./infra/native/build.sh
uv run --locked harbor run -p tasks/invoice-total -a oracle --max-retries 0 --force-build
uv run --locked harbor run -p tasks/checkout-recovery -a oracle --max-retries 0 --force-build
SAPI_RUN_NATIVE_TESTS=1 uv run --locked --extra harbor --extra benchmark \
  python -m unittest tests.test_native_tasks -v
```

Rebuild images after editing shared sources. `--force-build` rebuilds Harbor's
small task layers but cannot rebuild their base images. The build script creates
images only; it neither generates tasks nor starts trials. Tests retain logs and
native trial artifacts under `reports/native-phase1/`. The complete suite includes
nop/wrong YAML, all invoice observations, stateful checkout, saved calibration,
fake runtime transport failures, private placement and hostile transfer checks.
Checkout uses hash-pinned saved simulated calibration answers through an explicit
adapter bound to each fresh run, with no judge dispatch. The resulting
`calibration-transport.json` distinguishes this projection from historical replay
and measured model quality. No model or paid judge is used. Author environments
explicitly have no network; trusted verifier/world connectivity is separate.

Native task assets are included in the source distribution. They are intentionally
checkout-only; use the existing `package-tasks` command for installed-wheel task
staging. `NATIVE_PARITY.md` records remaining migration limits and deletion
candidates; Phase 1 removes no legacy architecture.
