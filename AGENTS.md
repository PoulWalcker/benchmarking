# Agent guide

Keep context small. Read this file first, then open only the document related to the change:

- architecture or module boundaries -> `docs/ARCHITECTURE.md`
- commands, scenarios, tests, reports, UI -> `docs/DEVELOPMENT.md`
- YAML semantics or supported workflow behavior -> `docs/PROFILE.md`

## System model

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
     |
  AUTHORING (optional)
```

`coordinate/` selects cases, binds fixtures, enforces budgets, packages tasks, and runs the stages. Harbor is orchestration around the experiment, not a workflow implementation.

## Ownership boundaries

| Concern | Location | Rule |
| --- | --- | --- |
| Shared contracts/profile | `src/sapi_config_lab/*.py`, `bindings.yaml` | backend-neutral project semantics |
| Author | `src/sapi_config_lab/author/` | produces definitions; does not compile them |
| Compile | `src/sapi_config_lab/compile/` | validates and produces artifacts; no execution or verdicts |
| Execute | `src/sapi_config_lab/execute/` | runs artifacts and records engine evidence; no business verdicts |
| Evaluate | `src/sapi_config_lab/evaluate/` | scores recorded evidence; does not become the executor |
| Coordinate | `src/sapi_config_lab/coordinate/` | composes stages and owns experiment flow |
| Independent verifier | `verification/` | recomputes acceptance independently; never imports compiler/runtime logic |
| Scenario data | `benchmarks/NN-<scenario>/` | task definition, fixtures, reference config and scenario metadata |

`tests/test_boundaries.py` enforces import direction. Update architecture and the test together when a boundary intentionally changes.

## Invariants

- Execution success, acceptance, and quality score are separate facts.
- Evaluation must use recorded evidence. Do not make acceptance depend on rerunning the candidate.
- The independent verifier must not reuse compiler or operation implementations to compute expected answers.
- Evaluator-only material must not be exposed to the candidate agent.
- Evidence is immutable once recorded. Derived evaluation files may reference evidence; they must not rewrite it.
- Keep backend-specific behavior behind `WorkflowBackend`. Do not add registries, universal IRs, or placeholder backends without a real second implementation.
- Share a helper after it has multiple real callers, not in anticipation of future reuse.
- Model-call budgets and source/provenance gates are safety boundaries, not convenience checks.

## Common change paths

### Change workflow semantics

1. Update `docs/PROFILE.md` only if the supported contract changes.
2. Update validation/compiler/runtime code required by that contract.
3. Add behavioral and boundary tests.
4. Run the local checks and a real Docker control if container behavior changed.

### Add or change a benchmark scenario

Follow `docs/DEVELOPMENT.md#adding-a-project-scenario`. Do not create a new documentation file for the scenario.

### Add another benchmark source

Treat source, environment, and evaluator as adapters around the same experiment flow. Add a separate architecture only if the execution model is genuinely different.

## Checks

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
uv run --locked python infra/check_distribution.py
```

For container/runtime behavior:

```bash
./run.sh --scenario <name>
```

## Documentation policy

Documentation describes durable truth, not the history of how the project reached it.

- `README.md` = entry point and happy path.
- `AGENTS.md` = repository rules for coding agents.
- `docs/ARCHITECTURE.md` = why the system is split this way.
- `docs/DEVELOPMENT.md` = how to work with it.
- `docs/PROFILE.md` = the supported workflow contract.

Do not add status reports, migration diaries, experiment summaries, evaluation write-ups, or one-off research notes to `/docs`. Put run output in `reports/`, committed proof in `evidence/`, and historical discussion in Git/issues.

Do not duplicate CLI help or implementation details that are easier to discover from code. Document constraints, invariants, ownership, and non-obvious workflow.
