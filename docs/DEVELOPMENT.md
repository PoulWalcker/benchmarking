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
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
uv run --locked python infra/check_distribution.py
```

They are fast and deterministic and prove nothing about real n8n. For that run the unpaid control suite:

```bash
./run.sh                          # default scenarios
./run.sh --scenario <name> ...    # any scenarios, hosted ones included
```

A control is valid only when `oracle` passes and `nop` fails. Fix the instrument before spending model calls.

## CLI

`uv run --locked sapi-lab --help` is the source of truth for options.

| Command | Purpose | Model calls |
| --- | --- | --- |
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

Internal commands (`execute`, `package-tasks`, `transport`, `bridge`, `simulator-worker`) run inside containers or under another command.

## Machine configuration

Defaults describe one Docker Desktop host. Override them through the environment; a CLI option, where one exists, overrides the environment.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SAPI_LAB_ROOT` | discovered from the working directory | the checkout experiments read |
| `SAPI_WRAPPER_URL` | `http://127.0.0.1:8765/run` | local model wrapper (`--upstream`) |
| `SAPI_WRAPPER_MODEL` | `gpt-6-astra` | model the wrapper must report; a mismatch fails closed |
| `SAPI_CONTAINER_HOST` | `host.docker.internal` | how a task container reaches host services (e.g. `172.17.0.1` on Linux) |
| `SAPI_LISTEN_HOST` | `0.0.0.0` | where hosted-simulator services listen for containers |
| `SAPI_BRIDGE_PORT` | `18765` | live Agency bridge (`live --bridge-port`) |
| `SAPI_UI_BRIDGE_PORT` | `18766` | UI Agency bridge (`ui --port`) |
| `SAPI_N8N_URL` | `http://localhost:5678` | local n8n editor |
| `SAPI_N8N_CONTAINER` | `n8n-n8n-1` | local n8n container (`ui --container`) |
| `SAPI_STAGING_DIR` | system temp | staging for task packages; must be mountable by Docker |

The coordinator sets the container protocol variables itself: `SAPI_LLM_MODE`, `SAPI_CASE_NAME`, `SAPI_BRIDGE_URL` (Harbor verifier env) and `SAPI_EXPECTED_SUBMISSION_SHA256` (replay packages). n8n inherits only `PATH`, `HOME`, `TMPDIR`, `TZ`, `LANG` and `LC_ALL` plus a run's binding.

## Adding a scenario

Add `benchmarks/NN-<name>/` with a `scenario.json` (fields in [ARCHITECTURE.md](ARCHITECTURE.md#one-scenario-registry)), a reference `config.yaml` that obeys [PROFILE.md](PROFILE.md) and `generation/FORMAT.md`, and an `instruction.md`.

A **fixture** scenario adds the public `task.md` and evaluator-only `cases.json`, then extends the verifier:

- role contract in `verification/scenario_contracts.py` (and `roles.py` when needed);
- business obligations in `verification/scenario_business.py`, recomputed from fixture inputs, never imported from the operation under test;
- a rubric card only for a quality question binary acceptance does not answer.

A **hosted** scenario names its pinned upstream challenge under `provenance` and adds `authoring-notes.md` and its own `bindings.yaml`; the upstream scorer is its evaluator.

Cover profile validity, packaging, a positive case and a plausible bad result the verifier rejects, then run `./run.sh --scenario <name>`. Do not add a docs file per scenario.

## Model-authored definitions

`./run-generation.sh` gives a model the task, `generation/FORMAT.md`, `generation/PROFILE.md` and the operation catalog, once per attempt, with no repair. The answer is a candidate like any other: compiled, executed and independently verified. Runtime model steps are stubs during generation. The wrapper does not disable its CLI tools: the prompt forbids them and recognized tool markers in its stderr reject the attempt, which is an audit, not a sandbox. `tests/test_packaging.py` pins every generation prompt's hash; a prompt change is an experiment change.

For a hosted scenario the gate after authoring is admission: the YAML compiles, stays within `runtime_model_calls` and keeps the upstream deadline.

## Live model execution

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab live --stub-report <control>/report.json \
  --max-calls N --wrapper-evidence <identity.json> [--submissions-manifest <selection.json>]
```

Before any dispatch, `live` requires a passing control report for the same sources and image, an unpaid stub replay, a ledger reservation per case and an inspected wrapper identity. The identity records each wrapper file's hash; files are read at their recorded path unless `--wrapper-file NAME=PATH` names the local copy (NAME is the file's basename). Hashes are always checked. Hosted scenarios also need `--judge-model`; each judge call is reserved.

`sapi-lab evaluate --record reports/<run>/environments/<job>/<scenario> --judgement <reply.json>` re-scores a hosted trial from a saved judgement without a model call.

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
| Did the required behavior hold? | the verifier's `report.json` and each case's `acceptance.json`, or the upstream `report.json` |
| How good was the output? | `evaluation.json`; `not_evaluated` is missing, not zero |

New runs go to `reports/` (ignored). Commit to `evidence/` only what a durable claim cites, and never edit it afterwards.

## Documentation

One fact, one owner: boundaries in `ARCHITECTURE.md`, workflow here, YAML semantics in `PROFILE.md`, the entry path in `README.md`, coding rules in `AGENTS.md`. History lives in Git and `evidence/`, not in `docs/`.
