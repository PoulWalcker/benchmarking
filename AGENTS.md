# Agent guide

Read this file first, then only the document the change needs:

- boundaries, the scenario registry, runs, evidence -> `docs/ARCHITECTURE.md`
- commands, configuration, adding a scenario, reading runs -> `docs/DEVELOPMENT.md`
- YAML semantics -> `docs/PROFILE.md`

## System model

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
  AUTHOR (optional)
```

`coordinate/` sequences the stages; each stage owns its own logic.

| Concern | Location | Rule |
| --- | --- | --- |
| Shared contracts | `src/sapi_config_lab/*.py`, `bindings.yaml` | backend-neutral; imports no stage |
| Author | `author/` | produces definitions only |
| Compile | `compile/` | artifacts only: no execution, no verdicts |
| Execute | `execute/` | runs artifacts, records engine evidence; owns host tools and `HostConfig` |
| Evaluate | `evaluate/` | scores recorded evidence; never reruns |
| Coordinate | `coordinate/` | orchestration; nothing imports it |
| Verifier | `verification/` | recomputes acceptance from fixtures; imports only itself |
| Benchmarks | `benchmarks/NN-<name>/` | the only scenario registry, via `scenario.json`; `evaluation/` holds its evaluator-only contract and rubric data |

`tests/test_boundaries.py` enforces import direction; change it together with `docs/ARCHITECTURE.md` when a boundary intentionally moves.

## Invariants

- Execution, acceptance and quality stay separate facts; `not_evaluated` is null, never zero.
- Evaluation reads recorded evidence; evidence is written once and derived files sit beside it.
- Candidates see public task material only: cases, references and hosted evaluator modules stay out of their containers.
- Benchmark origin is `provenance` data in `scenario.json`; code branches on `environment` and `evaluator`.
- Machine-specific values come from `HostConfig` (`SAPI_*`) or CLI options; reproducibility pins live beside their owner.
- Model calls are reserved in the ledger before dispatch, live dispatch fails closed on any identity or source mismatch, and oracle/nop controls gate paid work.
- A shared helper needs two real callers; a second backend precedes any registry or abstract base.
- A broad `except` belongs only at a real process, server or cleanup boundary; its file is listed under `BLE001` in `pyproject.toml` per-file-ignores, and the code carries no `noqa` comments.
- Comments state why; one-line docstrings are the norm.
- Prompt-bearing files (`generation/FORMAT.md`, `generation/PROFILE.md`, `task.md`, `prompt-extension.md`, `authoring-notes.md`, `bindings.yaml`, `execute/agency-prompt.md`) are pinned by recorded prompt hashes (generation prompts in `tests/test_packaging.py`); editing one is an experiment change.

## Common changes

- **Workflow semantics**: update `docs/PROFILE.md` if the contract changes, then validator/compiler/runtime, then behavioral tests; run a Docker control if container behavior changed.
- **Scenario**: follow `docs/DEVELOPMENT.md#adding-a-scenario`; no new docs file.
- **Docs**: current truth only, one owner per fact. Runs go to `reports/`, cited evidence to `evidence/`, history to Git.

## Checks

```bash
uv run --locked sapi-lab check   # tests, ruff, ruff format, mypy, distribution
./run.sh --scenario <name>       # when execution, packaging, containers, evidence or verification change
```
