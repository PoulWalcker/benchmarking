# 03-fixture-ownership

Findings: F01, F13. Dependencies: 02. Status: pending.

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
