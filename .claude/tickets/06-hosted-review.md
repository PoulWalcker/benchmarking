# 06-hosted-review

Findings: F11. Dependencies: 04, 05. Status: complete.

## Why

Review exporter only finds container rubric files and omits hosted evaluations and null quality.

## Current behavior

coordinate/runs.py::load_trials; evaluate/review_export.py

## Target behavior

Record authoritative report association and discover/render hosted common results with links to provider details; keep fixture rubric rendering.

## Done when

Both hosted scenarios and a null-quality evaluator export readable facts without modifying evidence or inventing scores.

## Tests

Review-export and run association tests.

## Non-goals

No provider-specific universal renderer or relocation framework.

## Outcome

Run trial rows record the authoritative evaluation path. Review discovery prefers that association, supports the historical host layout, and renders common hosted facts plus evaluator-detail links. Null quality stays absent; hosted rewards and evidence are never rewritten. Existing fixture rubric behavior is retained.

Validation: 64 targeted tests passed (`reports/cleanup-06-tests.log`), including authoritative association, historical discovery, scored/null-quality exports, immutability and boundary checks. Ruff, formatting and mypy passed. Diff inspected: no provider-specific rendering or evaluation-stage import of coordination.
