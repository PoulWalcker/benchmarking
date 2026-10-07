# 02-fixture-bindings

Findings: F03 (with F01/F13). Dependencies: 01. Status: pending.

## Why

Fixture-specific catalogs are lost between selection, prompts, container execution and evidence reconstruction.

## Current behavior

coordinate/packages.py::scenario_catalog/generation_prompt/stage_tasks; observe.py; live_evidence.py; lifecycle.py

## Target behavior

Use the selected catalog throughout fixture authoring, staging, execution, lifecycle validation and graph reconstruction. Preserve default catalog bytes and prompt hashes.

## Done when

A restricted fixture catalog reaches all relevant consumers; default nine fixture scenarios behave unchanged.

## Tests

Packaging, observer, lifecycle, live evidence and boundary tests.

## Non-goals

No new catalog schema or operation loading mechanism.
