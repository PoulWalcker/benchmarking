# Development

## Setup

```bash
uv sync --locked --extra harbor
```

The project is pinned to Python `3.14.8` through `.python-version`. Use `--locked` so `uv.lock` and `pyproject.toml` must agree.

Docker is required for the real n8n/Harbor control path. A local Node/n8n installation is only needed for the interactive UI workflow.

## Fast checks

Run these before a Docker experiment:

```bash
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
uv run --locked python infra/check_distribution.py
```

These checks are fast and deterministic. They do not prove real n8n behavior.

For runtime/container behavior use the control suite:

```bash
./run.sh
# or one scenario
./run.sh --scenario <name>
```

A useful control requires both sides:

- `oracle` must pass;
- `nop` must fail.

If either control is wrong, fix the measuring instrument before running model-authored candidates.

## CLI

The CLI is the source of truth for command options:

```bash
uv run --locked sapi-lab --help
uv run --locked sapi-lab <command> --help
```

Main human-facing commands:

| Command | Purpose | Model calls |
| --- | --- | --- |
| `compile` | validate one YAML and produce backend artifact + mapping | no |
| `build` | compile project reference configs | no |
| `harbor` | run deterministic Docker/Harbor controls | no |
| `generate` | author workflow YAML with a model after controls | yes |
| `live` | replay admitted submissions with live model operations | yes |
| `select` | select generated submissions for replay by a fixed rule | no |
| `evaluate` | re-evaluate one recorded trial; judge only with `--dispatch-judge` | only if dispatched |
| `ui` | import/view workflows in local n8n; live execution may call models | sometimes |
| `lifecycle` | operate the durable candidate lifecycle controller | depends on mode |
| `review-export` | derive a human-readable analysis beside recorded results | no |

Commands listed by the CLI as internal are container/composition entry points. Do not build developer workflow around calling them directly from the host.

## Adding a project scenario

Do not create a new docs file for a scenario. A scenario is code/data plus verifier coverage.

### 1. Add the scenario directory

```text
benchmarks/NN-<scenario>/
  task.md          public task given to an authoring agent
  config.yaml      reference workflow definition
  instruction.md   task-container/oracle instruction
  cases.json       evaluator fixtures
  scenario.json    runtime/model budgets and scenario metadata
```

Add `prompt-extension.md` only when the scenario genuinely needs task-specific authoring instructions.

The reference `config.yaml` must obey `docs/PROFILE.md` and the model-facing rules in `generation/FORMAT.md`.

### 2. Add independent verification

Extend the existing verifier instead of creating scenario-specific verifier packages:

- role/topology contract in `verification/roles.py` when needed;
- business result checks in `verification/scenario_business.py`;
- rubric card only when there is a quality question not captured by binary acceptance.

Expected answers must be recomputed from fixture inputs in verifier code. Do not import the workflow operation that produced the candidate answer.

### 3. Add tests

Cover at least:

- profile/definition validity;
- scenario discovery and packaging;
- positive business behavior;
- negative or corruption behavior that proves the verifier can reject a plausible bad result;
- budget/provenance rules for any model operations.

### 4. Run deterministic controls

```bash
./run.sh --scenario <scenario-name>
```

Only after the oracle/nop instrument is valid should you spend model calls on authoring or live runtime execution.

## Model-authored definitions

The author receives the bounded profile plus the operation catalog. It is not asked to write arbitrary n8n JSON.

```bash
./run-generation.sh
```

The generated YAML is still a candidate submission. It must pass the same compile, execute, evidence, and independent verification path as any other definition.

Do not automatically repair a failed answer unless the experiment explicitly measures repair/refinement. Silent repair changes the experiment.

## Live model execution

Live runtime operations go through the existing wrapper/Agency bridge and use explicit call budgets.

Before dispatching:

1. run deterministic controls;
2. inspect the command plan with `--help`/prepare modes where available;
3. verify the expected wrapper/model identity and source pins;
4. confirm the configured call budget.

A stale source or wrapper identity should fail closed instead of silently running a different experiment.

## Imported simulator scenarios

`benchmarks/10-checkout-recovery` and `benchmarks/11-crm-lead-qualification` are ordinary scenarios whose task, environment and evaluator come from a pinned upstream AutoWFBench revision. They run through the same `harbor`, `generate`, `select` and `live` commands.

- They need the pinned source in `.cache/autowfbench/<revision>` and `uv sync --extra benchmark`.
- Their directory holds `scenario.json` (challenge, budgets, control reward), `bindings.yaml`, `authoring-notes.md` and the reference `config.yaml`; there is no `cases.json`.
- After authoring, the gate is admission only: the YAML compiles, stays within the runtime model cap and keeps the original deadline.
- In `harbor` and `live`, the host starts a fresh simulator per trial and evaluates the recorded evidence with the upstream scorer. `live` needs `--judge-model`; each judge call is reserved in the ledger.
- `sapi-lab evaluate --record reports/<run>/environments/<job>/<scenario> --judgement <reply.json>` re-scores a trial from a saved judgement without calling a model.

## Local n8n UI

To inspect compiled workflows without treating the UI as benchmark evidence:

```bash
uv run --locked sapi-lab ui open --all
```

For one prepared live workflow:

```bash
uv run --locked sapi-lab ui open benchmarks/09-priority-support-brief/config.yaml --live
```

Interactive UI runs are useful for debugging and inspection. Benchmark claims should point to recorded experiment evidence instead.

## Reading a run

Do not infer correctness from a successful n8n execution.

Look for three separate outputs:

| Question | Evidence |
| --- | --- |
| Did the engine run? | execution record / native n8n evidence |
| Did the required behavior hold? | `acceptance.json` and verifier report |
| How good was the output? | optional `evaluation.json` / judge result |

A negative corruption check is successful when the bad candidate is rejected.

`not_evaluated` quality is missing evaluation, not score zero and not acceptance failure.

## Evidence and historical results

New results belong in `reports/`. Reports are local and ignored by Git.

Commit only evidence that supports a durable claim worth keeping in the repository. Put it under `evidence/` with enough metadata to identify the source run.

Do not maintain a second prose history in `/docs`. Git already records documentation/code history, and experiment artifacts record experiment history.

## Documentation changes

When behavior changes, update the smallest canonical document that owns the rule:

- module/system boundary -> `ARCHITECTURE.md`
- developer workflow -> `DEVELOPMENT.md`
- YAML/runtime semantics -> `PROFILE.md`
- first-run entry point -> root `README.md`
- coding-agent invariant -> root `AGENTS.md`

Avoid new docs for temporary status, a single experiment, a migration that has already happened, or an implementation detail discoverable from code.
