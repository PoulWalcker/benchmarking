# 04-hosted-contracts

Findings: F06, F08, F14, F17. Dependencies: 01. Status: pending.

## Why

Environment metadata loads scorer contracts; evaluator API carries argparse; malformed normalized results fail late; native execution and completion are conflated.

## Current behavior

coordinate/providers.py; evaluation.py; packages.py; execute/autowfbench.py; hosting.py; execute/n8n.py

## Target behavior

Read verified environment metadata in its adapter; use small typed reevaluation options and boundary validation; expose native outcome and completion separately while retaining legacy execution meaning. Correct touched misleading terminology without changing durable schemas.

## Done when

Task/limit lookup does not freeze a scorer; invalid evaluator results fail at dispatch; null quality valid; historical execution field remains compatible and native success is explicit.

## Tests

Provider, evaluator, hosting, packaging/prompt pin and boundary tests; full check.

## Non-goals

No evaluator hierarchy; no new mandatory narrative acceptance; no schema renaming.
