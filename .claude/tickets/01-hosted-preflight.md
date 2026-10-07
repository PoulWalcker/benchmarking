# 01-hosted-preflight

Findings: F02. Dependencies: None. Status: complete.

## Why

Hosted LLM catalogs advertise operations without deterministic stubs; required replay reaches a missing JS function.

## Current behavior

compile/n8n.py::compile_n8n; compile/runtime-fragment.js::applyStep; coordinate/hosted_worker.py::admit; coordinate/live.py::main/check_trials

## Target behavior

Declare actual local implementation availability; reject unsupported stub/local compilation clearly. Hosted candidates use unpaid live-mode compilation/admission, with no fabricated model answers; unchanged oracle/nop reference execution still gates paid work.

## Done when

crm.plan and incident operations admit in live mode; unsupported stub compilation fails before execution; hosted admission validates submission identity and limits; fixture replay and reference controls remain mandatory.

## Tests

Node/compiler regression; hosted admission and live preflight tests; packaging and boundaries.

## Non-goals

No synthetic task answers, operation plugin loader or backend framework.

## Outcome

Missing stub confirmed by the actual Node helper (`TypeError: operations[step.uses] is not a function`). The compiler now rejects unsupported local/stub operations before execution; live HTTP/Agency capabilities remain available. Hosted preflight uses the existing admission worker and proves compilation/limits only; fixture stub replay and fresh oracle/nop execution still gate paid work. No task answers were fabricated.

Validation: 74 targeted tests; full check passed (399 tests, Ruff, formatting, mypy, distribution); real CRM oracle/nop and transport controls passed at `reports/cleanup-01-crm/report.json` (oracle 0.732). Unpaid Harbor admission is recorded at `reports/cleanup-01-preflight-verified/report.json`. An initial preflight correctly rejected import whitespace drift; formatting restored the exact tested manifest before rerunning.
