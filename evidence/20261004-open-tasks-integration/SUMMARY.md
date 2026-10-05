# Open-task completion

All requested bounded experiments completed and passed their independent gates. The project now supports native bounded refinement (04), an explicit durable lifecycle controller (05), a usable local n8n UI bridge, and two new composition tasks on the unchanged catalog. Specification provenance is pinned and independently verifiable. These results include fresh model-authored YAML and real n8n/model execution, with original returned bytes preserved.

| Phase | Accepted authorings | Actual Agency runtime calls | Actual WBS repair calls | Total / admitted ceiling | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| UI demonstration | 0 | 4 | 0 | 4 / 4 | Manual execution 61 succeeded; 25 native nodes |
| 04 reply refinement | 3 | 4 | 0 | 7 / 9 | Positive accepted at attempt 1; impossible limit exhausted 3 attempts with no output |
| 05 digest lifecycle | 1 | 5 | 1 | 7 / 9 | Original Callback/Cron passed; intentional mutation rejected, model-created revision 2 passed Callback/Cron |
| G1 billing + bulletin packet | 2 | 2 | 0 | 4 / 4 | Both live input cases passed |
| G2 two audience briefs | 2 | 10 | 0 | 12 / 14 | Both live input cases passed using five calls each |
| **This new batch only** | **8** | **25** | **1** | **34 / 40** | **34 observed wrapper attempts/completions; no runner retries** |

Provider-internal requests/retries and cost remain unknown. These numbers exclude historical experiments and this assistant's ordinary analysis. All planned model-authoring/live cases ran; unused caps were not spent.

04 compiles bounded attempts and early exit into one native graph. The positive live case needed no correction; rejection→correction→acceptance was observed in deterministic native controls. Live exhaustion proved actual prior-draft/feedback carry and no accepted result after the third rejection. 05 retains its complete original model-authored definition; separate recorded UTC schedule overlays produced actual Cron runs at 19:24 and 19:40 on 2026-10-04. Its explicitly labeled wrong-output copy caused one real WBS repair and a new revision; it was not substituted for blank-slate authoring.

G1 returned correct `total_minor` values of 1798 AED minor units (17.98 AED) and 172 EUR minor units (1.72 EUR), each with count 2 and the corresponding article preview/IDs. G2 kept product and audience evidence separate for both inputs. Both attempts within each family chose the same structural shape. Different safe graph alternatives were proved by manual native controls; G2's five-step generated graph also differed from both manual controls through shared product analysis and inline writer objects. This demonstrates finite catalog composition, not arbitrary semantic equivalence or structural diversity between authoring attempts.

## Evidence and usable examples

| Example | Original generated YAML | Execution/output evidence |
| --- | --- | --- |
| 04 | [Selected original](refinement-evaluation-2/generation/jobs/generated-attempt-1/revise-answer__potobY3/agent/submission.yaml) | [Live report](refinement-evaluation-2/live/report.json), [actual attempt histories](refinement-evaluation-2/live/refinement-history-review.json) |
| 05 | [Original lifecycle](lifecycle-evaluation-3/series/submission.yaml) | [Original Callback/Cron](lifecycle-evaluation-3/after-authored/authored/report.json), [repair and final Cron](lifecycle-evaluation-3/after-mutation/mutation/report.json) |
| G1 | [Selected original](generalization-evaluation/series/billing-bulletin-packet/submission.yaml) | [Two live outputs and correlations](generalization-evaluation/series/billing-bulletin-packet/live/report.json) |
| G2 | [Selected original](generalization-evaluation/series/two-audience-briefs/submission.yaml) | [Two live outputs and correlations](generalization-evaluation/series/two-audience-briefs/live/report.json) |

[Independent review](INDEPENDENT-REVIEW.md) covers provenance, native persisted data, outputs and final claims. [Initial text-quality review](AGENT-QUALITY-REVIEW.md) preserves the earlier overlong support-reply finding; newer successful outputs do not erase it. Exact executed command arrays, source environments and caps are in the [04 plan](refinement-evaluation-2/admission-plan.json), [05 plan](lifecycle-evaluation-3/admission-plan.json) and [G1/G2 plan](generalization-evaluation/admission-plan.json). New experiments require fresh directories/ledgers; completed or failed ledgers are not resume recipes.

## Local UI

The new inactive workflow is [available in local n8n with execution 61](http://localhost:5678/workflow/a306069acac34f50/executions/61); [screenshot](ui-demo/result-ui.jpg), [native verification](ui-demo/run/verification.json). The helper is stopped. Viewing the workflow/history makes no model call; another live run requires a fresh prepared workflow and grant.

From the repository root, choose a new output directory:

```sh
.venv/bin/sapi-lab ui prepare reports/20261004-open-tasks-integration/ui-demo/supplied-ui-config.yaml --output-dir reports/ui-next --port 18766
.venv/bin/sapi-lab ui serve reports/ui-next --max-attempts 4 --seconds 600 --wrapper-evidence reports/20261004-open-tasks-integration/wrapper-identity.json
```

Import `reports/ui-next/workflow.json` as a new workflow. Keep `ui serve` in the foreground, check its `/health` from the n8n container, then explicitly execute the manual trigger and inspect node inputs/outputs. The unchanged existing wrapper must be running at port 8765; port 18766 is the separate project Agency adapter. The supplied UI YAML preserves the prior model-authored graph with its documented 120→600-second deadline overlay. A grant admits one native workflow/execution pair. See [the UI guide](../../docs/N8N-UI.md). An imported 05 candidate JSON additionally requires the durable lifecycle controller; it is not the complete lifecycle by itself.

## Verification and preservation

- Final settled-code pass: **186 local tests**, Ruff lint/format (**80 files**), mypy (**49 modules**), and final distribution/privacy build (**10 private sentinels excluded**, 153 sdist files, 44 wheel files). Logs: `final-local-tests.log`, `final-lint.log`, `final-format.log`, `final-types.log`, `final-distribution.log`.
- [Native inventory](native-execution-inventory.json): **113 fresh native execution identities**, 77 engine successes and 36 error outcomes across all probes, expected negatives, retained failures and live runs. Copied checkpoints are deduplicated by identity and metadata hash. This is not 113 business successes.
- [Preservation](PRESERVATION.json): all **11,200 historical files**, nine original configs, catalog, pinned spec, wrapper/config hashes, **12 original containers** and **six original workflows** are unchanged. Protected workflow `haUQEcB818FQjfkH` is unchanged. The sole workflow addition is inactive `a306069acac34f50`; all experiment containers were removed. No commits or pushes were made.
- Earlier unpaid failures remain intact: 04 exhausted-Result evidence shape, 05 missing timezone database, and 05 Harbor report-directory fsync. Narrow fixes and fresh zero-spend ledgers preceded paid calls; no acceptance or generated YAML was weakened to pass.

Executed immutable manifests: [UI](ui-demo/source-manifest.json) `80834f97…`, [04](refinement-fixed-freeze/source-manifest.json) `7ccb4dbc…`, [05](lifecycle-final-freeze/source-manifest.json) `4f522fb6…`, [research](generalization-freeze/source-manifest.json) `52706105…`. [Current source](final-source-manifest.json) is `72a7e7216765ee32b481ad2a91dc6be36ed2cb1c4c906884a9e5ef91bd7d004c` (152 files); its [five documentation-only differences](final-source-delta.json) from executed research are recorded separately.

The [specification manifest](../../provenance/spec-source.json) records repository, exact commit/path and snapshot hash. Offline verification and explicit fetch/compare are documented in [SPEC-SOURCE](../../docs/SPEC-SOURCE.md). The pinned commit `06ddd3333109cea8a2cb3071609070d7a3c0d3ff` and snapshot are unchanged; the actual pinned fetch matched byte-for-byte and produced an empty diff.

No requested bounded research step remains unrun. Future production claims remain out of scope: finite operations/fixtures, bounded lexical prose checks rather than general factual entailment, limited crash/schedule coverage, and an explicit external lifecycle controller. Independent agent content review passed for the new live outputs; it is not human pilot approval. WBS prompt/candidate/source/findings hashes were recomputed, but full raw wrapper response/stderr was not retained, so those two hashes are recorded metadata only. Broader authoring samples, human pilot review and stronger operational/semantic validation remain future research, not hidden prerequisites for these completed bounded experiments.
