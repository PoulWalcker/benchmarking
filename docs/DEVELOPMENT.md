# Development

## Setup

```bash
uv sync --locked --extra harbor --extra benchmark
uv run --locked sapi-lab fetch-source autowfbench
```

Python is pinned to 3.14.8; `--locked` keeps `uv.lock` authoritative. Docker with
Compose is required for native controls. `fetch-source` reads checkout's source pin
under `tasks/checkout-recovery/provenance/` and verifies every cached byte. Changed
cache content is refused. Native verifier builds fetch their pinned private sources
and dependencies independently; the host cache supports offline scoring.

## Checks

```bash
uv run --locked sapi-lab check
./run.sh --scenario invoice-total --scenario checkout-recovery
```

`check` runs unit tests, lint, formatting, types and direct/sdist wheel installation
checks. It uses no Docker or models. `run.sh` additionally builds source-identified
native images, runs transport controls and submits direct Harbor oracle/nop trials.
Invoice is the sole default when scenarios are omitted. `--skip-build` requires an
existing build record matching every current source and selected image ID.

Freeze all source-manifest files, including documentation, before guarded controls.
Write progress only under `reports/` until the run finalizes. Oracle acceptance and
its expected reward must pass; nop must reject. Checkout nop can retain null quality
and Harbor's expected missing-reward exception. An unrelated exception cannot pass
a negative control.

Opt-in controls exercise native privacy, runtime failure and fresh-world behavior:

```bash
SAPI_RUN_NATIVE_TESTS=1 uv run --locked python -m unittest tests.test_native_tasks -v
SAPI_RUN_DOCKER_TESTS=1 uv run --locked python -m unittest tests.test_harbor_checkout -v
SAPI_RUN_DOCKER_TESTS=1 uv run --locked python -m unittest tests.test_refinement_verification -v
```

These controls are unpaid. Checkout control mode uses saved calibration projection
and explicit fake runtime transport. Protected real model transport is retained but
paid-unverified; mocked wrapper tests do not establish real model performance.

## CLI

`uv run --locked sapi-lab --help` and each command's `--help` list current options.

| Command | Purpose | Model calls |
| --- | --- | --- |
| `benchmarks` | list native task metadata without importing evaluators | no |
| `check` | required local checks | no |
| `compile` / `build` | compile one YAML / all task references | no |
| `harbor` | unpaid control suite, also exposed by `run.sh` | no |
| `generate` | one model-authored YAML answer per attempt after controls | yes |
| `select` | freeze the first-started eligible submission for each task | no |
| `live` | replay selected/reference YAML with protected model operations | yes |
| `evaluate` | score recorded current native evidence | only with `--dispatch-judge` |
| `fetch-source` | fetch and verify a task-owned upstream pin | no |

Internal `execute`, `transport` and `bridge` commands need their execution environment.
The compact scope removes `ui`, `lifecycle`, `review-export`, `package-tasks`, and
archived evaluation snapshot options. Historical result reading remains available.

## Compilation and installed use

```bash
uv run --locked sapi-lab compile tasks/invoice-total/solution/config.yaml --output /tmp/invoice.json
uv run --locked sapi-lab compile /tmp/candidate.yaml --scenario invoice-total --output /tmp/candidate.json
```

A checkout scenario supplies its trusted bindings and operation source. Detached
compilation also works in an ordinary installed wheel with both explicit files:

```bash
sapi-lab compile candidate.yaml --bindings bindings.yaml --operations operations.js --output workflow.json
```

A detached definition inherits no catalog. Use `--llm-mode live --bridge-url URL`
when compiling model operations without a local stub; compilation calls no model.
Wheels include generic code, runtime resources and independent verification.
Native benchmark task assets and image builds require the matching editable
checkout. The source distribution remains a source archive; installing its wheel
does not bundle task resources.

## Direct native Harbor

```bash
uv run --locked sh infra/native/build.sh
uv run --locked harbor run -p tasks/invoice-total -a oracle --max-retries 0 --force-build
uv run --locked harbor run -p tasks/checkout-recovery -a oracle --max-retries 0 --force-build
```

The build script creates images only. Harbor receives canonical task directories,
without generation or materialization. Rebuild after changing sources: Harbor's
`--force-build` rebuilds task layers, not their local base images. Research CLI
controls record all base-image identities and the full source manifest.

Checkout native modes are explicit: `control` uses stub execution for zero-model
workflows and fake runtime transport for model steps; `admission` compiles only;
`live` requires a protected bridge and explicit judge identity. All worlds are fresh.
The calibration adapter records its provenance separately from measured quality.

## Generation and fixed selection

`./run-generation.sh` gives the model the task, unchanged generation contracts and
catalog once per attempt. There is no repair or feedback. `--catalog scenario` is
a separately hash-pinned invoice arm containing reference-used operations; full is
the default and checkout has no reduced arm. `tests/test_native_generation.py` and
`tests/test_packaging.py` pin exact prompt bytes. Editing prompt material changes
the experiment.

The wrapper's JSON and bounded answer are validated before upload; exact answer
bytes are preserved. Recognized tool markers in stderr reject an attempt. This is
an audit, not a sandbox disabling the wrapper's CLI tools. Raw stderr is not saved.
Invoice generation executes independent fixture evaluation. Checkout generation
only admits compilation/call cap/deadline; its verdict facts remain null.

```bash
uv run --locked sapi-lab select --source-report <generation>/report.json \
  --scenario invoice-total --output <selection.json>
```

Selection chooses the first-started attempt, never the first passing replacement.
It rejects tied ordering, contradictory/missing current normalized verdicts and
changed YAML, prompt, generation, native or case bytes. Loading the selection
recomputes the choice from the frozen source report. Upload-only replay calls no
generator.

## Live execution and reservations

```bash
uv run --locked sapi-lab live --stub-report <control>/report.json \
  --max-calls N --wrapper-evidence <identity.json> \
  --submissions-manifest <selection.json> --judge-model <model>
```

Before paid work, controls must match current sources and all selected image IDs.
Fixture replay or world compilation admission must pass. `--preflight-only` stops
before runtime/judge dispatch. Total runtime plus judge cost is checked before any
reservation. Every call is durably reserved before dispatch; an unknown outcome
is never released. `--series-dir` and fixed `--series-ceiling PHASE=N` share budgets
across runs. Failed and unknown outcomes remain distinguishable.

The inspected wrapper identity binds model and source file hashes. `--wrapper-file
NAME=PATH` relocates a named file without changing its required hash. Runtime grants
bind operation and occurrence, and evidence/audit reconciliation checks the exact
compiled graph, cases and request/completion/response identities. Runtime reservation
closes before judging; a host reservation is forwarded without double accounting.

## Current offline evaluation

```bash
uv run --locked sapi-lab evaluate --record <trial>/verifier --output <new-directory>
uv run --locked sapi-lab evaluate --record <trial>/verifier --output <new-directory> \
  --judgement <saved-reply.json>
```

The current task evaluator reads frozen evidence; it never starts n8n or a world.
`native-task.json` must match complete current sources, options and exact submission
bytes. Output must be new and outside original evidence. Checkout can reuse a saved
judge reply with matching contract/run identity. Fresh judging requires explicit
`--dispatch-judge`; calibration additionally requires `--calibration CASE
--judge-model MODEL`. Shared-series options preserve existing source-bound budgets.
An actual judge timeout leaves the reservation unknown and blocks retry.

Original Harbor result/reward and execution evidence stay untouched. Derived judged
results live beside them and remain explicitly associated with the trial. Source
mismatch refuses before evaluation; use the exact recorded source revision when
reproducing a current native experiment. Archived snapshot execution is deferred.
Older or Phase 1 native records lacking complete identity remain readable, but
cannot claim current guarded re-evaluation.

## Reading a run

Harbor owns `reports/<run>/jobs/<job>/<trial>/`. Research `report.json` adds identity,
ledger and selection facts plus compact references, including partial trials. Use
`evaluate.records.load_trials(job)` for current native files, or
`evaluate.records.read_report(path, root=relocated_run)` for saved reports. The latter
preserves the original document and returns normalized trial facts separately;
retired task names do not need runnable metadata. Missing execution, terminal or
quality observations remain null. Historical evidence is unchanged. Markdown export
is deferred.

## Machine configuration

`execute/host.py::HostConfig` owns machine defaults and corresponding `SAPI_*`
environment overrides. Wrapper URL/model, container host, listen host and bridge
port remain configurable. `SAPI_LAB_ROOT` selects an editable workspace, whose
package must match the source tree. The coordinator supplies scoped container
protocol values for mode, case, bridge and expected submission hash. n8n inherits
only the executor's allowed environment plus the explicit run binding.

## Adding a scenario

1. Add a native `tasks/<name>/` directory with `task.toml`, public instruction,
   bindings, operations, environment/tests Dockerfiles and an oracle solution.
2. Keep business planning/evaluation in that task. Fixed `tests/main.py` composes
   explicit callbacks; fixed `experiment.py` supplies prompt, plan and evaluate
   actions required by the research CLI. `metadata.sapi` contains policy only.
3. Add explicit public/verifier image copies to `infra/native/Dockerfile` and the
   build targets. Public images exclude private fixtures, source pins and references.
   Declare verifier-only worlds in `tests/docker-compose.yaml`; an environment
   Compose file is automatically merged by Harbor into the author environment.
4. Use the existing protected YAML transfer profile. Add meaningful independent
   evaluation and source/prompt identity tests, then run `check` and unpaid controls.

There is no central task-name registry or generic hook manifest. Shared mechanisms
need two real callers. Preserve independent verification: expected business values
must not come from the runtime operations being tested.

## Retained and deferred scope

Full existing compiler/profile validation and lowering, bounded refinement and its
independent verifier remain. Lifecycle policy syntax and event binding compile, but
lifecycle/WBS repair execution is deferred. Custom n8n UI, Markdown review export,
archived evaluator execution and task wheel resources are also deferred. Their code
and historical evidence remain in Git; current native evaluation/judging remains
active. See [ARCHITECTURE.md](ARCHITECTURE.md) for ownership and trust boundaries.
