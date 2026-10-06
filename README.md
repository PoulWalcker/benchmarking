# Sapi Config Lab

Research harness for testing whether agent-authored workflow definitions can be compiled to real n8n, executed in isolation, and evaluated independently.

```text
task -> author definition -> compile -> execute -> evidence -> evaluate
```

Harbor orchestrates isolated benchmark runs. n8n is the implemented workflow backend. The project implements the bounded `sapi-lab/v0` profile inspired by the pinned Sapiens specification; it is not a full Sapiens runtime.

AutoWFBench is integrated as another benchmark source/environment/evaluator, not as a separate project architecture.

## Quick start

Requirements:

- `uv >= 0.12.4`
- Docker with Compose
- Python `3.14.8` selected through `.python-version`
- Node.js only for local n8n UI workflows

```bash
uv sync --locked --extra harbor
./run.sh
```

`./run.sh` is the main unpaid control gate: local checks, pinned n8n image, transport probes, and Harbor oracle/nop trials.

Useful commands:

```bash
# Local checks
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy

# Compile without execution
uv run --locked sapi-lab compile benchmarks/01-invoice-total/config.yaml --output /tmp/workflow.n8n.json

# Model-authored workflow definitions; dispatches model calls
./run-generation.sh

# Inspect all CLI commands
uv run --locked sapi-lab --help
```

Commands such as `generate`, `live`, benchmark live modes, judged calibration, and some lifecycle/UI paths can dispatch model calls. Read `docs/DEVELOPMENT.md` before using them.

## What is being measured

Keep three facts separate:

1. **Execution** — did the engine run the workflow successfully?
2. **Acceptance** — did the independent verifier confirm the required behavior?
3. **Quality** — did an optional rubric or judge rate the output well?

One does not imply another. Harbor reward for the project scenarios is based on acceptance, not on optional quality scoring.

## Project layout

```text
.
├── src/
│   └── sapi_config_lab/
│       ├── author/        # Model-driven definition authoring and repair
│       ├── compile/       # Validated YAML → backend artifact
│       ├── execute/       # Artifact execution and runtime transport
│       ├── evaluate/      # Host-side scoring and report analysis
│       └── coordinate/    # CLI, case selection, budgets, packaging, experiment runners
│
├── benchmarks/            # Project benchmark scenarios and fixtures
├── verification/          # Independent acceptance and rubric logic
├── generation/            # Model-facing authoring format and operation catalogs
├── harbor/                # Shared Harbor task templates
├── infra/                 # Pinned runtime image and distribution checks
├── provenance/            # Pinned upstream specification material
├── evidence/              # Committed historical evidence excerpts
└── reports/               # New local run output; ignored by Git
```

## Documentation

Read only what you need:

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system boundaries, stages, scenario registry, evidence model.
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — setup, commands, adding scenarios, running and interpreting experiments.
- [`docs/PROFILE.md`](docs/PROFILE.md) — supported `sapi-lab/v0` YAML semantics and limits.

Coding agents should start with [`AGENTS.md`](AGENTS.md).

`generation/FORMAT.md` is model-facing prompt material, not general project documentation. `evidence/README.md` documents the evidence archive itself.

## Evidence and reproducibility

New runs write to `reports/`. Selected historical artifacts may be committed under `evidence/` so claims can point to immutable data.

Historical evidence proves what happened in that recorded run. It does not prove that the same result holds for the current source tree, model configuration, or environment.

Pinned source revisions, source hashes, runtime versions, and task packages exist to make those boundaries explicit.
