# 03-fixture-ownership

Findings: F01, F13. Dependencies: 02. Status: complete.

## Why

Independent business semantics, planning exceptions, guarded call prediction, rubric extraction and freshness are scattered through generic machinery.

## Current behavior

verification/verify.py; business.py; scenario_business.py; rubric_facts.py; coordinate/generate.py::fresh_case_overlay

## Target behavior

Localize trusted fixture policies and freshness with explicit evaluator ownership; ordinary new fixture tasks add independent evaluator code and benchmark data without editing generic dispatch functions. Keep verifier independence and existing obligations/probes.

## Done when

Generic planner/scorer/generator no longer branches on benchmark names; all existing positive/negative/corruption tests pass; generated overlays preserve current relationships.

## Tests

Verifier/business/role/rubric/refinement/lifecycle/budget/freshness tests; packaging isolation; full check.

## Non-goals

No business-rule DSL, dynamic plugin system, universal lifecycle or reuse of implementation under test.

## Outcome

Independent fixture composition is now in `verification/fixture_evaluators.py`; business branches became named checks, rubric rendering moved to `fixture_prose.py`, and the four composed benchmarks explicitly select their existing freshness function. Generic planning/scoring/generation no longer selects rules by benchmark name. Refinement task limits remain with its independent evaluator; lifecycle remains the existing narrow procedure. No runtime operation is imported by verification.

An ordinary new-name fixture regression adds only a trusted evaluator entry and fixture/role data, then uses the unchanged plan, evidence, model-budget and corruption machinery. Existing arithmetic, source-fact obligations and mutation probes are preserved. Independent operation-output declarations and historical graphless-record allowlist are intentionally kept.

Validation: 103 targeted tests; full check passed (404 tests, Ruff, formatting, mypy, distribution), `reports/cleanup-03-check.log`. Real Docker lifecycle/composed controls: `reports/cleanup-03-fixtures/report.json`.
