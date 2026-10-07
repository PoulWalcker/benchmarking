# 07-transport-controls

Findings: F16, F18. Dependencies: 04. Status: complete.

## Why

Two HTTP clients follow redirects despite shared policy; nop tolerates unrelated Harbor errors.

## Current behavior

author/agent.py; coordinate/hosted_worker.py; coordinate/evaluation.py::control_passed; controls.py

## Target behavior

Use shared no-redirect HTTP policy; allow only the exact expected missing-reward exception for unscored hosted nop.

## Done when

Redirect targets receive no forwarded request; deterministic nop requires reward 0 without exception; unrelated exceptions fail controls; unscored nop remains null.

## Tests

Real HTTP redirect regression; nop exception classification tests; full check and all eleven unpaid controls.

## Non-goals

No configurable redirect policy; no broad exception tolerance.

## Outcome

Both author and hosted worker now use the shared no-redirect HTTP client. Real HTTP tests prove neither follows a redirect; the worker does not forward its bearer. Hosted nop permits only the exact missing-reward exception for an unscored, rejected trial with no reward; null-quality deterministic nop requires zero reward and no exception.

Audit refinement: observed pinned Harbor jobs exit zero even when the trial reports the expected missing reward (`reports/cleanup-01-crm/report.json`, confirmed again in ticket 05). The former blanket nonzero-job allowance was therefore removed, not replaced by another exception bypass.

Validation: 32 targeted tests passed, including redirects, nop error classification, provider and boundary checks. Full check and all eleven control results are recorded in the final cleanup report. Diff inspected: one shared transport policy; no provider-name branch added.
