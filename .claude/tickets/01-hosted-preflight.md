# 01-hosted-preflight

Findings: F02. Dependencies: None. Status: pending.

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
