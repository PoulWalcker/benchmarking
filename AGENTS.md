# Agent guide

Read this file first, then only the document the change needs:

- ownership, benchmark loading, runs or evidence -> `docs/ARCHITECTURE.md`
- commands, configuration or adding a benchmark -> `docs/DEVELOPMENT.md`
- YAML semantics -> `docs/PROFILE.md`
- independent verifier contracts -> `verification/README.md`

## System model

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
  AUTHOR (optional)
```

Benchmarks own operations, worlds and independent business evaluation. Core owns
the generic stages below; `coordinate/` sequences them. `harbor_integration/`
adapts tasks to external Harbor 0.21.0, which owns trial infrastructure.

| Concern | Location | Rule |
| --- | --- | --- |
| Shared contracts | `src/sapi_config_lab/*.py` | backend-neutral; imports no stage |
| Author | `author/` | produces definitions only |
| Compile | `compile/` | artifacts only: no execution, no verdicts |
| Execute | `execute/` | runs artifacts, records engine evidence; owns host tools and `HostConfig` |
| Evaluate | `evaluate/` | scores recorded evidence; never reruns |
| Coordinate | `coordinate/` | orchestration; nothing imports it |
| Verifier | `verification/` | generic independent evidence checks with explicit callbacks; imports only itself |
| Integration | `harbor_integration/` | task translation and invocation; only it imports Harbor |
| Tasks | `tasks/<name>/` | Own domain assets and native Harbor configuration; current experiment descriptors declare their public/trusted/reference closure |

`tests/test_boundaries.py` enforces import direction; change it together with `docs/ARCHITECTURE.md` when a boundary intentionally moves.

## Invariants

- Execution, acceptance and quality stay separate facts; `not_evaluated` is null, never zero.
- Evaluation reads recorded evidence; evidence is written once and derived files sit beside it.
- Candidates receive declared public material. Trusted evaluator/world code and private fixtures belong to the verifier; references belong to oracle solutions.
- Select descriptors and trusted entrypoints explicitly; benchmark configuration stays opaque to generic core mechanisms.
- Machine-specific values come from `HostConfig` (`SAPI_*`) or CLI options; reproducibility pins live beside their owner.
- Model calls are reserved in the ledger before dispatch, live dispatch fails closed on any identity or source mismatch, and oracle/nop controls gate paid work.
- A shared helper needs two real callers; a second backend precedes any registry or abstract base.
- A broad `except` belongs only at a real process, server or cleanup boundary; its file is listed under `BLE001` in `pyproject.toml` per-file-ignores, and the code carries no `noqa` comments.
- Comments state why; one-line docstrings are the norm.
- Prompt material (generation contracts, public benchmark instructions/catalogs/authoring prompts and `execute/agency-prompt.md`) is hash-pinned; changing it is an experiment change.

## Common changes

- **Workflow semantics**: update `docs/PROFILE.md` if the contract changes, then validator/compiler/runtime, then behavioral tests; run a Docker control if container behavior changed.
- **Scenario**: follow `docs/DEVELOPMENT.md#adding-a-scenario`; no new docs file.
- **Docs**: current truth only, one owner per fact. Runs go to `reports/`, cited evidence to `evidence/`, history to Git.
- **Controls**: freeze every source-manifest file, including docs, before a guarded run; write progress only under `reports/`.

## Checks

```bash
uv run --locked sapi-lab check   # tests, ruff, ruff format, mypy, distribution
./run.sh --scenario <name>       # when execution, packaging, containers, evidence or verification change
```
