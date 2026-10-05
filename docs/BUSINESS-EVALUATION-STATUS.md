# Two-task pilot: implementation and measured status

The shared infrastructure is implemented and unpaid native controls pass for
both pinned simulators. **Live CRM evaluation is not closed:** both authoring
attempts returned HTTP 500 after about 180 seconds. Neither produced an eligible
YAML submission, so CRM runtime and candidate judging were never dispatched.
This timing is consistent with the unchanged wrapper's 180-second limit; the
server exception was not independently confirmed. No business score is assigned.

| Area | Implemented and evidenced | Remaining limit |
| --- | --- | --- |
| 1. Common task contract | TaskDefinition and frozen original packages specify inputs, actions, result, constraints, criteria; both use one runner/backend | Two configured tasks only |
| 2. Business environments | Fresh original Checkout/CRM simulators; actual CRM state changes in native controls | No production integrations; live CRM authoring blocked |
| 3. Independent verification | Original protected checks, negative controls and seeded inputs; CRM native seeds 0/1 and Checkout compatibility pass | Seed variation is not additional independent tasks |
| 4. Frozen scoring | Original weights and checks preserved; missing/error judgements stay unscored; execution and score are separate | No invented score for failed authoring |
| 5. Semantic judge calibration | Separate original judge; predefined misleading-success control measured 0/4 semantic points, evidence=no and honesty=no | Positive Checkout evidence is historical, not a new prospective calibration; broad calibration/reliability remains open |
| 6. Traceable score | Criterion, answer, weight, points and evidence references retained; real Checkout 9.33/10 and Harbor 0.933 remain immutable | CRM controls' 0.732 uses an explicit simulated judge, not a live CRM score |
| 7. Comparable series | Versioned prompts/source/task/rubric/image/models/budgets, failed attempts and stage counts retained; controls separated | One live evaluated candidate across two task integrations; no pooled ranking/statistical claim |
| 8. Bounded actions | Explicit retry only on retryable update error, operation IDs, known-receipt replay, conflicting-key rejection, ambiguous-outcome latch; create/send duplicate controls pass | Receipts are live-session scoped, not durable cross-process exactly-once delivery |

New model dispatches: **3** = two failed authorings + one successful negative
calibration judge. CRM runtime calls: **0**. CRM candidate judge calls: **0**.
The initial ceiling was four; a separately versioned compact-authoring follow-up
was explicitly admitted with an amended ceiling of five. No further retry ran.
The earlier successful Checkout used six calls and remains a separate historical
run, giving nine observed dispatches across the combined evidence.

Validation: 239 local tests, Ruff, mypy and package privacy checks passed before
the focused planned-versus-started reporting correction. Ten focused regression
checks plus Ruff/mypy passed after that correction. Fresh v2 native controls
passed with unchanged frozen sources before the follow-up dispatch. The first
failed report remains immutable; the series correctly counts its planned but
unstarted candidate as zero native attempts.

Working unpaid controls, after the setup in BUSINESS-EVALUATION.md:

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab benchmark --task crm --mode controls --report-dir reports/crm-controls-new
```

The complete live command is implemented, but the observed authoring transport
limitation must be resolved before expecting it to finish:

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab benchmark --task crm --mode live --report-dir reports/crm-live-new
```

Do not automatically rerun it. The next bounded task is read-only diagnosis of
the wrapper timeout/error using safe metadata, or an explicitly approved authoring
adapter change. Preserve the current wrapper/model settings and both failures;
freeze a new plan before spending further model calls. Do not substitute the
scripted oracle and describe it as generated YAML.

Private results: `reports/two-task-validation-20261005/completion-summary.json`
and `series.json`; failed runs `reports/crm-live-20261005/` and
`reports/crm-live-20261005-v2/`; measured negative calibration
`reports/crm-calibration-20261005/`; native controls
`reports/crm-controls-20261005-seed0/`, `reports/crm-controls-20261005-seed1/`,
`reports/crm-controls-20261005-v2/` and `reports/checkout-compatibility-20261005/`.
This status document was written after the frozen runs; their evidence was not
rewritten. The final preservation summary records unchanged UI fingerprints and
the five n8n-related container identities. The unrelated vip-test-postgres
container is absent from the first CRM after snapshot; the cause is unknown,
so global container preservation is not asserted. No shared wrapper
configuration or original workflow was edited.
