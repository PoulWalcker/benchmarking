# 02-fixture-bindings

Findings: F03 (with F01/F13). Dependencies: 01. Status: complete.

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

## Outcome

The scenario catalog now reaches full/reduced prompts, trusted task bindings, observation, lifecycle admission/execution/repair, live graph reconstruction and dispatch prompt-hash reconciliation. Empty supplied catalogs fail closed rather than falling back. Default prompts are unchanged.

Validation: 79 targeted packaging/generation/selection/lifecycle/rebuilder/live/evidence/boundary tests passed, plus Ruff, formatting, mypy and diff inspection. Docker fixture controls run at the fixture-ownership checkpoint (03), which completes this shared area.
