# Sapi Config Lab

Research harness that tests whether agent-authored workflow definitions compile to real n8n, execute in isolation and pass independent evaluation.

```text
task -> author definition -> compile -> execute -> evidence -> evaluate
```

The workflow language is the bounded `sapi-lab/v0` profile, inspired by the pinned Sapiens specification; it is not a full Sapiens runtime. n8n is the implemented backend and Harbor isolates each trial.

## Quick start

Requirements: `uv >= 0.12.4`, Docker with Compose, and Python `3.14.8` (selected by `.python-version`).

```bash
uv sync --locked --extra harbor --extra benchmark
./run.sh
```

`./run.sh` is the unpaid control gate: local tests, the pinned n8n image, transport probes, and Harbor oracle/nop trials for the default scenarios. `./run.sh --scenario <name>` selects others; hosted scenarios first need `uv run --locked sapi-lab fetch-source autowfbench`.

```bash
uv run --locked sapi-lab compile benchmarks/01-invoice-total/config.yaml --output /tmp/workflow.n8n.json
uv run --locked sapi-lab --help
```

`generate`, `live`, judged evaluation and some `ui`/`lifecycle` paths dispatch model calls; read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) first. Machine-specific settings (model wrapper, ports, container routes, staging) are `SAPI_*` environment variables listed there.

## What is measured

1. **Execution**: did the engine run the workflow?
2. **Acceptance**: did an independent evaluator confirm the required behavior?
3. **Quality**: how did an optional rubric or judge rate it?

None implies another. Harbor reward follows acceptance, never optional quality.

## Layout

```text
src/sapi_config_lab/
├── author/       model-driven definition authoring and repair
├── compile/      validated YAML -> backend artifact
├── execute/      artifact execution, runtime transport and host tools
├── evaluate/     scoring and evaluation of recorded evidence
└── coordinate/   orchestration only

benchmarks/       one directory per benchmark, registered by its scenario.json
verification/     independent acceptance verifier, packaged into each task
generation/       model-facing authoring contract (prompt material)
harbor/           Harbor task templates
infra/            runtime image and distribution checks
provenance/       pinned upstream sources
evidence/         committed evidence extracts
reports/          local run output (ignored)
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): stages, the scenario registry, runs, evidence.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): setup, commands, configuration, adding scenarios, reading runs.
- [docs/PROFILE.md](docs/PROFILE.md): `sapi-lab/v0` semantics and limits.
- [AGENTS.md](AGENTS.md): rules for coding agents.

`generation/FORMAT.md` and `generation/PROFILE.md` are prompt material, not documentation; editing them changes recorded prompt hashes.

Historical evidence proves what happened in a recorded run, not that it still holds for the current tree. Pinned sources, versions, image identities and source manifests make that boundary explicit.
