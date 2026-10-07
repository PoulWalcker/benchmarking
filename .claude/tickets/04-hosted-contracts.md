# 04-hosted-contracts

Findings: F06, F08, F14, F17. Dependencies: 01. Status: complete.

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

## Outcome

Environment metadata now reads and hash-checks its pinned definition in the environment adapter without invoking scorer compatibility. Re-evaluation uses immutable explicit judge options, deterministic evaluators reject irrelevant judge switches, and common result shape is validated at dispatch. Null quality remains valid. New `native_execution` and `terminal_completion` trial/report observations distinguish native success from timely valid submission; historical AutoWFBench `result.execution` retains completion semantics. A real-host fake state evaluator accepts state with no narrative while recording native=true/completion=false.

Corrected CRM activation names and executor documentation. Hosted prompts deliberately drop universal simulator wording; only their two prompt pins changed (checkout `920472a6…`, CRM `79b6ae1e…`). Historical artifacts and schemas are untouched.

Validation: full check passed; see `reports/cleanup-04-check.log`. Real hosted controls run with ticket 05 at the next lifecycle checkpoint.
