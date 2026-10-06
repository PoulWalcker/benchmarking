# Documentation migration

This package replaces active documentation with a compact ternary structure.

## Active knowledge

Three documents own current-system knowledge:

1. `docs/ARCHITECTURE.md` — how the system is structured.
2. `docs/DEVELOPMENT.md` — how to operate and extend it.
3. `docs/PROFILE.md` — what workflow language is supported.

Root `README.md` is the entry point. `AGENTS.md` is the coding policy.

## Reference knowledge

External systems live under:

```text
docs/reference/
```

- `SAPIENS.md`
- `AUTOWFBENCH.md`

## Historical knowledge

Only durable conclusions stay active under:

```text
docs/history/RESULTS.md
```

Exact experiment diaries remain available in Git history and `evidence/`.

## Remove from active docs

The following current files should not remain as active documentation after
their still-current facts have been checked against the replacement files:

- `docs/AUTHORING.md`
- `docs/BUSINESS-EVALUATION.md`
- `docs/CHECKOUT-EVALUATION.md`
- `docs/EXTENSION-ACCEPTANCE.md`
- `docs/GENERALIZATION-EVALUATION.md`
- `docs/GENERATED-LIVE.md`
- `docs/GLOSSARY.md`
- `docs/LIFECYCLE-EVALUATION.md`
- `docs/LIFECYCLE.md`
- `docs/MIGRATION-PATHS.json`
- `docs/N8N-UI.md`
- `docs/REPORTS.md`
- `docs/RESEARCH-QUESTIONS.md`
- `docs/RETIRED.md`
- `docs/SCENARIO-EXPANSION.md`
- `docs/SPEC-SOURCE.md`
- `docs/AUTOWFBENCH-ENVIRONMENT.md`

The old `docs/history/` experiment diaries can also be removed from `main`
after confirming that any required revision/evidence pointer is represented by
`docs/history/RESULTS.md` or Git history.

## Important

Do not delete or rewrite:

- `evidence/`
- `provenance/`
- prompt-bearing benchmark data
- historical Git commits

The documentation cleanup should reduce active navigation and context size
without destroying recorded evidence.
