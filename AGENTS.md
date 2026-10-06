# Working in this repository

A workflow definition (`sapi-lab/v0` YAML) moves through three stages:

```text
definition → COMPILE → n8n artifact + step map
           → EXECUTE → recorded evidence (native n8n records, outputs, errors, timings)
           → EVALUATE → acceptance, quality score, report
```

A thin **coordinator** picks cases, binds fixtures, enforces budgets and runs
the stages in order. **Authoring** (a model writing YAML or repairing it) is a
fourth, separate concern: it produces definitions and never compiles them itself.

## Where each stage lives

| Stage | Location | May import |
| --- | --- | --- |
| Shared contracts | `src/sapi_config_lab/{contracts,profile,evidence,paths}.py`, `bindings.yaml` | nothing in the package |
| Compile | `src/sapi_config_lab/compile/` | shared contracts |
| Execute | `src/sapi_config_lab/execute/` | shared contracts |
| Evaluate (host side) | `src/sapi_config_lab/evaluate/` | shared contracts |
| Evaluate (independent verifier) | `verification/` | its own siblings; never `compile`, `execute`, `coordinate` |
| Authoring | `src/sapi_config_lab/author/` | shared contracts |
| Coordinate | `src/sapi_config_lab/coordinate/` | anything above; nothing imports it except `__main__` |
| Benchmark definitions | `benchmarks/<scenario>/` | data only |

`tests/test_boundaries.py` enforces this table: `STAGES` assigns every module to
one stage, `ALLOWED` is the table's last column, and only coordination may load
modules through `importlib`. `KNOWN_VIOLATIONS` lists the edges that still break
a rule while the code moves into these directories; it may only shrink. Change
the table and the test together, never one alone.

## Rules

- **Compile** validates and produces the artifact and its mapping. It never starts
  a process, opens a socket or computes a verdict.
- **Execute** runs a compiled artifact and records what the engine did. Engine
  success (`execution.succeeded`) never implies business correctness.
- **Evaluate** reads recorded evidence and task criteria. It never launches n8n,
  Docker or a model to obtain the evidence it judges. A judge call happens only
  when explicitly requested, or from a saved judgement.
- **Three separate facts**: execution status, acceptance, quality score. Missing
  evaluation (`not_evaluated`, null score) is never a failure and never a zero.
- **Evidence is immutable once written.** Evaluation writes its own files
  (`acceptance.json`, `evaluation.json`, `report.json`) beside it, naming the
  evidence it read; corruption probes mutate copies.
- **Evaluator-only material** (`benchmarks/*/cases.json`, reference
  `config.yaml`, verifier code) never reaches a candidate agent's container.
  Generation packages are checked for this in `tests/test_packaging.py`.
- **Independent verification is not duplication.** The verifier recomputes
  expected answers in plain Python and must not import the compiler, operations
  or runtime. Do not "deduplicate" it against the workflow implementation.
- Keep backend-specific behavior behind `WorkflowBackend` (`contracts.py`).
  No plugin registries, universal IRs or placeholder backends.
- Share a helper only after it has two real callers; no catch-all `common` module.

## Adding a scenario

See `docs/AUTHORING.md`. A scenario is a `benchmarks/<scenario>/` directory plus
its role contract and business check in `verification/`.

## Never change

- `evidence/` and `docs/history/`: byte-exact historical records.
- Generation prompt text: changing it changes recorded prompt hashes.
- Frozen report schemas listed in `docs/GLOSSARY.md#frozen-schemas`.

## Checks

```bash
uv run --locked python -m unittest discover -s tests
uv run --locked ruff check src tests verification infra && uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
./run.sh --scenario <name>   # unpaid Docker control suite; the only proof for container behavior
```
