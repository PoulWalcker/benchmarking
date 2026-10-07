# 05-hosted-lifetimes-evidence

Findings: F09, F10. Dependencies: 04. Status: complete.

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

## Outcome

Evaluator-owned aggregate duration now composes into finish RPC, verifier and Harbor bounds. Live grants start after evaluator preparation and allow Harbor setup plus environment/fixture execution. Existing model-call, backend, provider-startup and transport safety ceilings remain owned by their stages. Multiple hosted execution attempts in one session are rejected explicitly.

A locked terminal recorder writes durable evidence on normal finish, deadline or close. Worker loss records unknown native outcome and no submission; timeout/abort never auto-dispatch a judge or create acceptance. Partial snapshot failures retain available transport/native observations and an explicit error inventory. Late finish cannot replace evidence; evaluation failure leaves it untouched.

Validation: 69 targeted tests, including actual timer expiry, concurrent finish/timeout, abort, collection/evaluator failures, and longer environment/evaluator budgets. Full check passed (417 tests), `reports/cleanup-05-check.log`. Real checkout oracle/nop/transport controls passed at `reports/cleanup-05-checkout/report.json`; reward remains 0.732.

Limit: host process/OS death before persistence is not crash recovery; upstream cancellation remains outside the current protocol. Receipt extraction is deferred to a second provider.
