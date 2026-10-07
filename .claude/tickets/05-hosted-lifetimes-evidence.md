# 05-hosted-lifetimes-evidence

Findings: F09, F10. Dependencies: 04. Status: pending.

## Why

Hosted grant, RPC and evaluator durations compose incorrectly; timer/close can discard frozen environment state if finish never arrives.

## Current behavior

coordinate/live.py; packages.py; runs.py; hosted_worker.py; providers.py; execute/hosting.py; evaluate/autowfbench.py

## Target behavior

Declare actual stage bounds and compose current flows; persist trusted evidence on timeout/abort exactly once with no fabricated output/acceptance; reject unsupported multiple hosted attempts.

## Done when

Longer declared hosted limits fit grant/worker/Harbor budgets; timeout/close preserve evidence; finish/timeout races cannot overwrite records; evaluator failure leaves evidence intact.

## Tests

Hosting timeout/abort/race regressions; budget/settings tests; provider integration; full check.

## Non-goals

No global timeout settings, scheduler, retry framework or shared receipt extraction.
