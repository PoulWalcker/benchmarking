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

A control is valid only when `oracle` passes and `nop` fails. Fix the instrument before spending model calls.

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
| `review-export` | write derived `analysis.md` beside recorded evaluations | no |
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

- `evaluation/contract.json`: the role contract (operations, input lineage, edges, output) that `verification/roles.py` binds any unambiguous step IDs to;
- independent business checks in `verification/`, registered in the evaluator-owned `fixture_evaluators.py` table; ordinary cases use the existing plan/evidence machinery. The entry also selects guarded-call expectations, optional rubric obligations/prose and optional freshness. Checks recompute from fixtures, never import runtime operations;
- `evaluation/rubric.json` only for a quality question binary acceptance does not answer, plus its evaluator-owned prose function in `verification/fixture_prose.py`.

`evaluation/` is evaluator data: packaging copies it into the task's trusted `tests/` and `.dockerignore` keeps it out of the image.

A scenario-specific `bindings` catalog is used by the author prompt, staged as `tests/bindings.yaml`, passed through fixture execution (including lifecycle), and used for live graph and prompt-hash reconciliation. The default catalog is unchanged.

Optional `harbor` settings in `scenario.json` (`agent_timeout_sec`, `verifier_timeout_sec`, `build_timeout_sec`, `cpus`, `memory_mb`, `storage_mb`; bounded integers, defaults in `coordinate/scenarios.py`) are rendered into `task.toml`. Staging refuses a `verifier_timeout_sec` smaller than the verifier's worst case: every planned execution in sequence at the executor's own import and execution ceilings, plus a fixed overhead. Add cases, then raise the timeout the error names. Each `harbor run` and the whole control suite are bounded by the same estimate: every trial at its build and agent limits plus its verifier estimate. A fixture package records the deadline it was sized for in `tests/budget.json`, and the verifier refuses to plan a definition with a longer one (`deadline_exceeds_budget`); hosted admission already requires the provider's trial limit.

A **hosted** scenario names an existing provider in `environment` and `evaluator`, plus that provider's own config (AutoWFBench: `provenance`), and adds `authoring-notes.md` and its own `bindings.yaml`.

Cover profile validity, packaging, a positive case and a plausible bad result the verifier rejects, then run `./run.sh --scenario <name>`. Do not add a docs file per scenario.

### Adding a hosted provider

1. `execute/<name>.py`: a `start` that returns an `EnvironmentSession` serving `POST /tools` receipts and `finalize()` evidence. When this second real provider needs the candidate listener, lift it from `execute/autowfbench.py` into `execute/hosting.py`.
2. `evaluate/<name>.py`: turns a recorded trial directory into `{execution, acceptance, quality}`.
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

## Reading a run

| Question | Evidence |
| --- | --- |
| Did the engine run? | `case.json` and the native n8n records beside it |
| Did the required behavior hold? | the verifier's `report.json` and each case's `acceptance.json`, or the hosted evaluator's `evaluation/report.json` |
| How good was the output? | `evaluation.json`; `not_evaluated` is missing, not zero |

New runs go to `reports/` (ignored). Commit to `evidence/` only what a durable claim cites, and never edit it afterwards.

## Documentation

One fact, one owner: boundaries in `ARCHITECTURE.md`, workflow here, YAML semantics in `PROFILE.md`, the entry path in `README.md`, coding rules in `AGENTS.md`. History lives in Git and `evidence/`, not in `docs/`.
