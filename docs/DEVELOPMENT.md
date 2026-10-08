# Development

## Setup

```bash
uv sync --locked --extra harbor --extra benchmark
uv run --locked sapi-lab fetch-source autowfbench   # hosted scenarios only
```

Python is pinned to `3.14.8` by `.python-version`; `--locked` keeps `uv.lock` authoritative. Docker is needed for the n8n/Harbor controls; a local n8n container only for `sapi-lab ui`.

`fetch-source` downloads the files pinned by `provenance/autowfbench-source.json` into `.cache/autowfbench/<revision>/` and verifies every byte. Nothing else fetches it, and a cache that differs from the manifest is refused, never repaired.

## Checks

```bash
uv run --locked sapi-lab check
```

It runs the unit tests, `ruff check`, `ruff format --check`, `mypy` and `infra/check_distribution.py`, prints each stage to stderr and exits 1 naming every stage that failed. The checks are fast and deterministic and prove nothing about real n8n. For that run the unpaid control suite:

```bash
./run.sh                          # default scenarios
./run.sh --scenario <name> ...    # any scenarios, hosted ones included
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
| `lifecycle` | the durable candidate lifecycle controller | with `--rebuilder-url` or live mode |
| `review-export` | write derived `analysis.md` in Harbor trials, discovering authoritative hosted reports from the run report; hosted rewards remain recorded facts | no |
| `fetch-source` | fetch and verify a pinned upstream source | no |

Internal commands (`execute`, `package-tasks`, `transport`, `bridge`, `hosted-worker`) run inside containers or under another command.

## Machine configuration

Defaults describe one Docker Desktop host. Override them through the environment; a CLI option, where one exists, overrides the environment.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SAPI_LAB_ROOT` | discovered from the working directory | the checkout experiments read |
| `SAPI_WRAPPER_URL` | `http://127.0.0.1:8765/run` | local model wrapper (`--upstream`) |
| `SAPI_WRAPPER_MODEL` | `gpt-6-astra` | model the wrapper must report; a mismatch fails closed |
| `SAPI_CONTAINER_HOST` | `host.docker.internal` | how a task container reaches those services (e.g. `172.17.0.1` on Linux) |
| `SAPI_LISTEN_HOST` | `127.0.0.1` | bind address of host services containers reach: Agency bridges and hosted trial hosts (`ui --host`); e.g. `0.0.0.0` on Linux |
| `SAPI_BRIDGE_PORT` | `18765` | live Agency bridge (`live --bridge-port`) |
| `SAPI_UI_BRIDGE_PORT` | `18766` | UI Agency bridge (`ui --port`) |
| `SAPI_N8N_URL` | `http://localhost:5678` | local n8n editor |
| `SAPI_N8N_CONTAINER` | `n8n-n8n-1` | local n8n container (`ui --container`) |
| `SAPI_STAGING_DIR` | system temp | staging for task packages; must be mountable by Docker |

The coordinator sets the container protocol variables itself: `SAPI_LLM_MODE`, `SAPI_CASE_NAME`, `SAPI_BRIDGE_URL` (Harbor verifier env) and `SAPI_EXPECTED_SUBMISSION_SHA256` (replay packages). n8n inherits only `PATH`, `HOME`, `TMPDIR`, `TZ`, `LANG` and `LC_ALL` plus a run's binding.

## Adding a scenario

Add `benchmarks/NN-<name>/` with a `scenario.json` (fields in [ARCHITECTURE.md](ARCHITECTURE.md#one-scenario-registry)), a reference `config.yaml` that obeys [PROFILE.md](PROFILE.md) and `generation/FORMAT.md`, and an `instruction.md`.

A **fixture** scenario adds the public `task.md` and evaluator-only `cases.json`, then extends the verifier:

`task.md` may show a public sample input. `cases.json` holds separate evaluator-only cases: `positive` means the observed outcome must match the case's expectation, even when that expectation is refinement exhaustion; `negative` means invalid input must be rejected. Case contents vary with the behavior being tested. Hosted scenarios use their provider's world instead of `cases.json`.

- `evaluation/contract.json`: the role contract (operations, input lineage, edges, output) that `verification/roles.py` binds any unambiguous step IDs to;
- independent business checks in `verification/`, registered in the evaluator-owned `fixture_evaluators.py` table; ordinary cases use the existing plan/evidence machinery. The entry also selects guarded-call expectations, optional rubric obligations/prose and optional freshness. Checks recompute from fixtures, never import runtime operations;
- `evaluation/rubric.json` only for a quality question binary acceptance does not answer, plus its evaluator-owned prose function in `verification/fixture_prose.py`.

`evaluation/` is evaluator data: packaging copies it into the task's trusted `tests/` and `.dockerignore` keeps it out of the image.

A scenario-specific `bindings` catalog is used by the author prompt, staged as `tests/bindings.yaml`, passed through fixture execution (including lifecycle), and used for live graph and prompt-hash reconciliation. The default catalog is unchanged.

Legacy `harbor` settings in `scenario.json` (`agent_timeout_sec`, `verifier_timeout_sec`, `build_timeout_sec`, `cpus`, `memory_mb`, `storage_mb`; bounded integers, defaults in `coordinate/scenarios.py`) are rendered into `task.toml`. Versioned packages use their benchmark-owned native task configuration. Staging refuses a verifier phase smaller than the verifier's worst case: every planned execution in sequence at the executor's own import and execution ceilings, plus a fixed overhead. Add cases, then raise the timeout the error names. Legacy jobs retain an aggregate estimate: every trial at its build and agent limits plus its verifier estimate. For legacy hosted execution, the worker allows the environment RPC budget plus the evaluator's aggregate duration for `/finish`; Harbor includes both RPCs and the environment window. Live grants allow Harbor setup before the fixture/environment execution window. Model-call and backend subprocess ceilings remain with their stage owners.

A fixture package records the deadline it was sized for in `tests/budget.json`, and the verifier refuses to plan a definition with a longer one (`deadline_exceeds_budget`); hosted admission already requires the provider's trial limit.

The hosted completion envelope requires a string `final_answer` and any declared string artifact, copied verbatim as Markdown. Native success, valid timely terminal completion, and evaluator acceptance are separate recorded facts.

A **hosted** scenario names an existing provider in `environment` and `evaluator`, plus that provider's own config (AutoWFBench: `provenance`), and adds `authoring-notes.md` and its own `bindings.yaml`.

Cover profile validity, packaging, a positive case and a plausible bad result the verifier rejects, then run `./run.sh --scenario <name>`. Do not add a docs file per scenario.

### Adding a hosted provider

1. `execute/<name>.py`: a `start` that returns an `EnvironmentSession` serving `POST /tools` receipts and `finalize()` evidence. When this second real provider needs the candidate listener, lift it from `execute/autowfbench.py` into `execute/hosting.py`.
2. `evaluate/<name>.py`: turns a recorded trial directory into `{execution, acceptance, quality}` and declares its aggregate `timeout_seconds`.
3. One entry each in `ENVIRONMENTS` and `EVALUATORS` (`coordinate/providers.py`), listing their host-only `modules`.
4. A `benchmarks/NN-<name>/` hosted scenario that names them.
5. Tests (prior art: `tests/test_hosted_provider.py`), then `sapi-lab check` and `./run.sh --scenario <name>`.

## Model-authored definitions

`./run-generation.sh` gives a model the task, `generation/FORMAT.md`, `generation/PROFILE.md` and the operation catalog, once per attempt, with no repair. `--catalog scenario` is an experiment arm, not the default: the catalog shows only the operations the scenario's reference uses, the report records the variant, the operations shown and their hashes, and its prompts are pinned separately. `sapi-lab package-tasks --mode generation --catalog scenario` stages those prompts without a model call. The answer is a candidate like any other: compiled, executed and independently verified. Runtime model steps are stubs during generation. The wrapper does not disable its CLI tools: the prompt forbids them and recognized tool markers in its stderr reject the attempt, which is an audit, not a sandbox. `tests/test_packaging.py` pins every generation prompt's hash; a prompt change is an experiment change.

For a hosted scenario the gate after authoring and before live dispatch is admission: the YAML compiles in live mode, stays within `runtime_model_calls` and keeps the provider's trial limit. It proves materialization capability and declared limits, not candidate execution or acceptance. Hosted model operations need no fabricated stub answer. Fresh oracle/nop reference executions still gate the instrument before paid dispatch.

## Live model execution

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab live --stub-report <control>/report.json \
  --max-calls N --wrapper-evidence <identity.json> [--submissions-manifest <selection.json>]
```

Before any dispatch, `live` requires a passing control report for the same sources and image, unpaid fixture stub replay or hosted compilation/admission, a ledger reservation per case and an inspected wrapper identity. The identity records each wrapper file's hash; files are read at their recorded path unless `--wrapper-file NAME=PATH` names the local copy (NAME is the file's basename). Hashes are always checked. A hosted evaluator that calls a judge also needs `--judge-model`; its `judge_calls` are reserved per trial.

`sapi-lab evaluate --record reports/<run>/environments/<job>/<scenario>` re-evaluates a hosted trial through its scenario's evaluator; for AutoWFBench, `--judgement <reply.json>` re-scores from a saved judgement without a model call.

## Local n8n UI

```bash
uv run --locked sapi-lab ui open --all
uv run --locked sapi-lab ui open benchmarks/09-priority-support-brief/config.yaml --live
```

Imports are inactive copies and never execute. A `--live` session arms one fresh copy with a budgeted bridge; execution stays a manual click. UI runs are for inspection, not benchmark evidence.

Compile and UI commands resolve a versioned benchmark's declared bindings and trusted
operation bundle when given its reference path. For a detached candidate, pass
`--scenario invoice-total` (or another selected benchmark); `--bindings PATH` still
provides an explicit catalog override. Historical detached forms without a selection
use the isolated `coordinate/legacy_compilation.py` compatibility facade: its fixed
installed catalog and operation bundle, never executable paths from candidate YAML.
That historical detached compile form remains usable without an experiment checkout;
explicit benchmark selection requires the editable workspace.
UI imports require local operations; workflows with remote tools need a Harbor trial.
Native `package-tasks --scenario ...` and versioned re-evaluation select descriptors
without loading the legacy execution registry. Legacy package and lifecycle forms
remain explicit compatibility entrypoints until retirement.

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
lists metadata without importing evaluators. It reports legacy versions as `null`;
Invoice-total and checkout-recovery use versioned manifests; nine other benchmarks remain legacy.
Existing command names and default selection are preserved. `package-tasks` and
the unpaid `harbor` controls materialize both through their declared entrypoints
and separate verifiers. Remaining experiment caller migration is still in progress.

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
`payload.dependencies.<alias>`. External Python dependencies currently consist of
the clean pinned runtime's standard library and PyYAML; additional local modules
must be declared in the manifest closure.

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


Checkout's normal-flow native Compose proof is opt-in:

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
Native task configuration sets build, author and verifier phase limits; admission
checks the planned executions against that resolved verifier phase. There is no
aggregate job watchdog on this path. Docker enforces CPU and memory limits; the
integration rejects disk, GPU and TPU requirements that this profile cannot enforce.

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
Existing lifecycle and legacy observation commands inject their compatibility
decision in `coordinate/legacy_lifecycle.py` until retirement; there is no CLI or
YAML option for selecting executable acceptance code.

The source distribution verifies every selected descriptor's declared file/dependency
closure, including its private source pins. Root pins still used by explicit legacy
readers remain in the source manifest through those benchmark declarations. The wheel
contains the generic runtime; experiment commands retain their existing requirement
for an editable checkout, as enforced by `workspace_root()`.


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
