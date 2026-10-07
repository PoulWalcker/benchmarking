# 07-transport-controls

Findings: F16, F18. Dependencies: 04. Status: pending.

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
