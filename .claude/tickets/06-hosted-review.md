# 06-hosted-review

Findings: F11. Dependencies: 04, 05. Status: pending.

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
