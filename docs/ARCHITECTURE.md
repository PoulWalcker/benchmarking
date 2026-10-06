# Project architecture

This reorganizes the existing research harness without extending `sapi-lab/v0`
semantics. Compilation and execution remain separate actions.

## Structural reference

The reference is [Sapiens CONTRIBUTING at the pinned revision](https://github.com/aleski-green/sapiens4/blob/06ddd3333109cea8a2cb3071609070d7a3c0d3ff/CONTRIBUTING.md):
organize code by responsibility, keep runtime independent of the host, avoid
import cycles, and preserve behavior during refactoring. The parent's Python
code is divided into `corpora/runtime/computer`. This project has three different
responsibilities: `core/runtime/interfaces`. Its scope does not require
copying the parent's desktop or web modules.

The `src` layout makes tests use the installed package rather than accidentally
importing a module from the repository root. `pyproject.toml` defines Python
requirements, dependencies, wheel building, and CLI entry points. `uv.lock`
records resolved dependencies; normal execution uses `--locked`. Harbor is an
optional extra, so compilation does not require its cloud SDKs. See
[uv projects](https://docs.astral.sh/uv/concepts/projects/config/) and
[locked sync](https://docs.astral.sh/uv/concepts/projects/sync/).

An independent agent reviewed the design using Matt Pocock's `ask-matt` and
`codebase-design` skills. The design uses a small set of modules without a
plugin registry or a hierarchy of empty classes.

## Responsibilities and dependencies

[AGENTS.md](../AGENTS.md) is the authoritative statement of the stages and
what each may import; `tests/test_boundaries.py` enforces it.

```mermaid
flowchart LR
  K[coordinate: cli, cases, observe, suites, packaging] --> X[execute: n8n, Agency, Harbor/Docker]
  K --> P[compile: n8n compiler]
  K --> E[evaluate: AutoWFBench scoring, reports]
  K --> A[author: model YAML, repair]
  K --> V[verification: independent verifier]
  P --> S[shared: contracts, profile, evidence, paths]
  X --> S
  E --> S
  A --> S
```

`paths.py` and `__main__.py` stay at the package root: `paths.py` asserts that
its own parent directory is `src/sapi_config_lab`, which is how a wheel
installed from somewhere else is rejected.

- `profile.py`: YAML parsing with duplicate-key rejection, references,
  dependencies, actors, and profile validation. It knows nothing about n8n,
  Harbor, or Docker. `bindings.yaml` is the single source operation catalog.
- `contracts.py`: the typed `WorkflowBackend` interface, `CompileOptions`,
  `RunBinding`, `CompiledWorkflow`, and `ExecutionRecord`. Compilation and
  execution are the two operations; expected business answers never enter this
  interface. `CompileOptions` holds only definition-time choices, so the same
  definition and options always give the same artifact. Per-run values (the
  absolute deadline the caller reserved, the admitted lifecycle event, the
  operation token) travel in `RunBinding` to `execute`, which passes them to
  n8n as environment variables and refuses a run that lacks a value the
  artifact requires.
- `compile/n8n.py`: `compile_n8n(config, bindings, ...) -> (artifact, mapping)`.
  Applies backend capability limits and generates JSON; `compile/refinement.py`
  expands bounded refinement. Adjacent JS files are included in the wheel. The
  compiler neither starts processes nor computes acceptance.
- `execute/n8n.py`: real n8n execution of a compiled artifact: a fresh
  database, import, execution, raw-data extraction and native artifact
  persistence. Success requires persisted n8n success and a Result node.
- `execute/agency.py`: the HTTP contract between an LLM node and the existing
  wrapper, prompt construction, JSON validation, safe auditing, and
  `start_bridge`/`stop_bridge`. The shared instruction is in `agency-prompt.md`.
- `execute/host.py`: the pinned Harbor, `harbor run` argv, run-to-log, image
  build, staging directory and job collection every suite uses.
- `coordinate/backend.py`: `N8nBackend`, the compile and execute stages behind
  one `WorkflowBackend`, and `default_backend()`, the sole production selection
  point. `coordinate/cases.py::run_case` compiles then executes one case and
  writes its `sapi-lab-execution/v1` record; it decides nothing about acceptance.
- `coordinate/observe.py`: runs a verifier-issued plan and records evidence.
- `coordinate/scenarios.py`: discovers `benchmarks/NN-<scenario>/`.
- `coordinate/controls.py`, `generate.py`, `live.py`, `benchmark.py`: the
  unpaid control suite and the paid tracks. They manage series, budgets,
  environment dependencies and reports.
- `evaluate/task_evaluation.py`: AutoWFBench scoring of a recorded run.
  `evaluate/benchmark_series.py`, `review_export.py`, `judge_calibration.py`
  summarize and present recorded evaluations.
- `author/agent.py` and `author/rebuilder.py`: one wrapper call that writes a
  YAML submission, and one bounded lifecycle repair request.
- `verification/business.py`: independent arithmetic and scenario criteria using
  logical `WorkflowObservation` values from `verification/contracts.py`.
- `verification/n8n_provenance.py`: checks native execution identity, persisted
  data, node envelopes, ordering, and correspondence with logical observations.
- `verification/verify.py`: `plan` names every definition the runtime must run
  for a submission; `evaluate` checks the recorded observation is complete and
  unaltered, then combines business acceptance and engine provenance and writes
  `acceptance.json` per case. It imports nothing from the package and never
  executes; `coordinate/observe.py` runs the plan in between.
- `verification/rubric.py`, `rubric_cards.py`, `rubric_facts.py`: a weighted
  quality score written to `evaluation.json` beside the acceptance report, under
  `sapi-lab-rubric-evaluation/v1`. It reads the acceptance verdict and never
  sets it; the reward still comes from the verifier's exit status alone.

[Adding a scenario](AUTHORING.md) lists every file a new task family touches
across these groups, with the gate to run after each one.

`tests/test_packaging.py` protects import direction and excludes reference
solutions from generation packages. Existing behavioral tests are retained.

## Adding another executor

Implement `WorkflowBackend.compile()` and `.execute()` alongside `runtime/n8n/`,
then select it at the composition point or inject it into the common CLI/runtime
interface. Keep the shared parser/profile and business criteria. Local test
adapters exercise this replacement without importing or launching n8n; they
demonstrate the seam and do not establish a second production executor.

A one-setting replacement is not promised. Native evidence, capabilities,
waiting/branching semantics, and provenance checks must be defined for the new
executor. The native provenance module currently checks n8n nodes and its version;
another executor must supply its own evidence adapter before acceptance can be
claimed. The structural Protocol requires no inheritance, registry, universal IR,
or placeholder Sapiens implementation.

Sapiens as an **agent authoring YAML** and Sapiens as a **YAML executor** are
separate experiments. This refactoring implements neither.

## Why reports still contain copies

`coordinate/packages.py::stage_tasks()` assembles packages from canonical sources:

| Source | Assembled package contents |
| --- | --- |
| `benchmarks/NN-<scenario>/instruction.md` | `instruction.md` for oracle/live |
| `benchmarks/NN-<scenario>/task.md` (+ `prompt-extension.md`), `generation/FORMAT.md`, profile, catalog | `instruction.md` for generation |
| `benchmarks/NN-<scenario>/config.yaml` or validated frozen replay mapping | `environment/base.yaml`, oracle/live/replay only |
| `harbor/templates/` | task.toml, test.sh, solve.sh; solve is oracle/live only |
| `verification/*.py` | hidden verifier modules in `tests/` |
| `benchmarks/NN-<scenario>/cases.json` | `tests/cases.json` for the selected scenario only |

These are distribution copies for isolated execution, not separate maintained
implementations. The destination directory must be new. Exact packages are saved
with results. Generation packages contain no solution/base.yaml; their containers
remove `/app/lab/configs` and `/app/scenario`. Tool restrictions in the existing
wrapper still rely on prompting and stderr auditing; this is not a secure benchmark.

## Environments

The host uses `uv sync --locked --extra harbor`, Python 3.14.8, and the project's
`.venv`. The compiler and bridge can be installed from a wheel and used outside
a checkout. Experiments require an editable installation of the selected checkout
and its fixtures/templates. A package/source mismatch is rejected before a run,
so recorded hashes cannot describe a different implementation. `SAPI_LAB_ROOT`
selects the checkout explicitly; otherwise it is discovered from the working
directory or editable installation. Runtime does not search for a checkout.

Containers use pinned n8n/Alpine and Python/PyYAML from `infra/Dockerfile`.
This is a separate minimal runtime without Harbor or development dependencies.
The package is supplied through `/app/lab/src` and `PYTHONPATH`; there is no pip
resolution during a case. Reports record container versions separately from
the host lockfile.

Python support is explicitly `>=3.14,<3.15`, with an exact 3.14.8 interpreter pin
matching the container. This is the verified minor series; it does not claim
support for older versions or 3.15 prereleases. The lock keeps Harbor 0.21.0 as
an optional extra. An immutable official Astral download catalog allows the
pin to work with uv 0.12.4 without changing the user's global Python or Harbor.

The source archive includes fixtures, templates, public specification provenance,
and reproduction settings. The public provenance allowlist contains `provenance/SapiensSpecNotation.hs`,
`provenance/spec-comparison.json`, and `provenance/spec-source.json`. The latter
records the upstream repository, immutable revision, source path, and snapshot hash;
[spec source maintenance](SPEC-SOURCE.md) verifies it offline and compares explicit upstream refs. Local environment
snapshots, reports, generated exports, validation outputs, and review dumps remain
in the working checkout and are excluded from Git, wheels, and source archives.
`infra/check_distribution.py` verifies this by building actual archives from a
temporary checkout containing private sentinel files.

`coordinate/provenance.py` hashes all public source modules and data, including
new files under the owned directories. Both Harbor manifests and generation
fingerprints use this same inventory. Per-run host versions are recorded locally
without environment variables or machine paths. Existing evidence is never
rewritten when paths or dependencies change.

### Manifests recorded before the `core`/`runtime`/`interfaces` split

A manifest keys every hash on a path relative to the repository root, so the
archived packages written before this restructure name files under
`src/sapi_config_lab/workflow/`, `src/sapi_config_lab/experiments/` and
`src/sapi_config_lab/runtime/contracts.py`. Those paths no longer exist. Their
manifests therefore no longer match a current checkout, and a series recorded
against them cannot be resumed or extended.

This break is accepted rather than papered over. No path translation is applied
when a manifest is read: a shim would make the code assert that a source is
unchanged when in fact it moved, which is exactly the claim a manifest exists to
make honestly. The consequences are bounded:

- Each archived package is self-contained and stays readable on its own terms.
  Re-reading past evidence needs nothing from this checkout.
- Runs recorded after the split form a new comparison group. Do not pool their
  numbers with pre-split runs in one series; compare within a group.

The 2026-10-06 move into `compile`/`execute`/`evaluate`/`author`/`coordinate`
and `benchmarks/` is a second break of the same kind, handled the same way:
runs recorded after it form their own comparison group, and the frozen
expansion and refinement selections, which require today's source manifest to
equal the one they recorded, cannot be resumed from this tree. Retired
experiments name the revision that reruns them in [RETIRED.md](RETIRED.md).

## Automated checks

Ruff checks lint and formatting; mypy checks all application and verifier Python
modules with `check_untyped_defs`. The only missing-import exception is the
optional third-party Harbor package, which supplies no typing marker. Local
tests exercise behavior, import direction, backend replacement, task packaging,
and source-inventory privacy. Distribution checks also verify installed resources.

`.github/workflows/checks.yml` runs those checks for pull requests and pushes.
The separate Docker/Harbor job is selected manually and uses deterministic
transport/oracle/nop controls. Live model calls are never part of CI. Neither job
uploads local execution artifacts.

## Commands

`uv run --locked sapi-lab --help` lists every subcommand in two groups, and each
one accepts `--help`. The first group is what a person runs; the second holds
entry points a container or another command invokes, which have no use at a host
checkout. This project uses `--locked`, not `--frozen`, everywhere: the CI
workflow `.github/workflows/checks.yml` runs `uv sync --locked` and
`uv run --locked` for all steps. `--locked` additionally verifies that `uv.lock`
still agrees with `pyproject.toml` and fails if it does not, which is what the
pinned-environment statements in the reports rely on. `--frozen` would skip that
check. Terms used below are defined in the [glossary](GLOSSARY.md).

### Commands you run

| Command | What it does | Also needs | Costs model calls |
| --- | --- | --- | --- |
| `compile` | Validate one YAML and write the n8n JSON plus its step-to-node map. It does not execute anything. | — | No |
| `build` | Compile every `benchmarks/*/config.yaml` into `--output-dir` and record which were rejected. | — | No |
| `harbor` | The unpaid control suite: build the pinned image, run transport probes, then the oracle and nop trials. | `--extra harbor`, Docker | No |
| `generate` | Model-authored YAML. Runs the control suite first and refuses to continue if it fails, then dispatches independent one-shot authoring attempts. | `--extra harbor`, Docker, the wrapper | Yes |
| `live` | Replay frozen generated submissions with live runtime operations, after an unpaid source-matched gate. | `--extra harbor`, Docker, the wrapper | Yes |
| `benchmark` | The AutoWFBench task evaluation, with `--task checkout` or `--task crm`. `--mode prepare` and `--mode controls` dispatch nothing. | `--extra harbor --extra benchmark`, Docker | `--mode live` only |
| `benchmark-series` | Build a comparison manifest from existing reports. It reruns nothing. | `--extra benchmark` | No |
| `benchmark-calibrate` | Compare the semantic judge against frozen evaluator-only controls. **It dispatches one real, paid judge call** unless `--prepare-only` is passed. | `--extra benchmark` | Yes, unless `--prepare-only` |
| `ui` | Compile and import graphs into local n8n, and prepare one bounded manual live session. | local n8n | Only when you press Execute on an LLM graph |
| `lifecycle` | The durable lifecycle controller: `register`, `callback`, `restore`, `status`, `tick`, `drain`, `serve` against a registry directory. Same entry point as the `python -m sapi_config_lab.coordinate.lifecycle` form used in the [lifecycle guide](LIFECYCLE.md). | — | Only with `--llm-mode live` or `--rebuilder-url` |
| `review-export` | Write a derived `analysis.md` beside recorded evaluations so the Harbor viewer can display them. It adds files only; `evaluation.json` is read-only to it. | a Harbor jobs directory | No |

The former `checkout` alias of `benchmark` was removed; see [retired runners](RETIRED.md).

### Internal and in-container commands

These run from the same entry point and are not hidden, but a host checkout is
the wrong place to type them: each needs an environment it does not get there.

| Command | What it does | Why it is not for you |
| --- | --- | --- |
| `execute` | Run one config through the selected engine and write a `sapi-lab-execution/v1` record. | Requires the real n8n CLI 2.41.5 on `PATH`, which the lab image provides and a host checkout normally does not. Use `harbor` or `live` for a real execution with evidence. |
| `package-tasks` | Assemble Harbor task packages into a new directory without running them. | Every experiment stages its own packages; a standalone package is for inspection only. |
| `transport` | Deterministic HTTP transport probes against a fake bridge. | Written to run inside the isolated lab image, where `run.sh` invokes it; outside that image it has no workspace to probe. |
| `bridge` | Foreground local Agency adapter in front of the existing wrapper. | `live` and `ui` start it themselves; a second one competes for the port and the budget latch. |
| `checkout-worker` | The trusted in-container verifier that runs frozen YAML through the real n8n backend. | The task container runs it as `python3 -m sapi_config_lab.coordinate.benchmark_worker`; it reads `/tests/connection.json` and writes `/logs/verifier`. |

`benchmark-calibrate` costs one model call. The module docstring in
`src/sapi_config_lab/evaluate/judge_calibration.py` states this, and the
command prints the model it is about to bill to stderr immediately before
dispatching. `calibration_fixture()` on its own is unpaid: it only swaps prose in
an already recorded trace. Budget for the dispatch accordingly.

## Command migration

| Previous command | Current command |
| --- | --- |
| `python3 lab.py compile ...` | `uv run --locked sapi-lab compile ...` |
| `python3 lab.py build` | `uv run --locked sapi-lab build` |
| `python3 scripts/run_harbor.py ...` | `./run.sh ...` |
| `python3 scripts/run_live.py ...` | `uv run --locked --extra harbor sapi-lab live ...` |
| `python3 scripts/run_generation.py ...` | `./run-generation.sh ...` |
| `python3 scripts/n8n_runtime.py ...` | `uv run --locked sapi-lab execute ...` (requires n8n CLI) |
| `python3 bridge/agency_bridge.py ...` | `uv run --locked sapi-lab bridge ...` |
| `python3 verification/sync_tasks.py` | Removed; packages are assembled before each run |
| `python3 -m unittest discover ...` | `uv run --locked python -m unittest discover -s tests -v` |

`build` now saves build-results.json alongside exports in `--output-dir`.
Old Python entry points were removed; both shell commands were retained.
See [MIGRATION-PATHS.json](MIGRATION-PATHS.json) for the complete file map.
Historical reports and source manifests are not rewritten to use new paths.

## Frozen generated replay

`coordinate/replay.py` validates the exact preselected historical artifacts and
provenance. `stage_tasks(mode="replay", submissions=...)` preserves their bytes.
`coordinate/live.py` reuses Harbor and the existing verifier/typed backend path,
with source/image gates, unpaid replay verification and serial live trials.

Replay mode is reachable only through `sapi-lab live --submissions-manifest`.
`package-tasks --mode` offers `oracle` and `generation` and deliberately not
`replay`: a replay package is defined by a submissions manifest that
`load_selection()` revalidates against a frozen source report, and by the image
tag that the same `live` run freezes and pins into its hashes. Staged on its own
with a default image tag, the package would carry no provenance anything checks.
The `--mode` help text says so at the command.
`execute/agency.py::DispatchAudit` owns the outgoing budget and failure latch;
`coordinate/live_evidence.py` reconciles it with independent native evidence.
There is no additional workflow executor. See [the replay command](GENERATED-LIVE.md).
