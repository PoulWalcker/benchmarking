# Scenario expansion integration

The occurrence-aware verifier and all four reference tasks are implemented and verified. The initial model evaluation stopped at SC-03's first authoring gate. Four of five submitted model-authored workflows were accepted; the fifth rejection exposed an undisclosed structural requirement, so it is not valid evidence that the model violated its visible task. The raw failed report and durable failure latch remain authoritative and unchanged.

SC-01 and SC-02 completed their generated-YAML live replay. SC-03 and SC-04 live evaluation was not run. SC-04 received separate unpaid reference controls after the paid series stopped. There were no repairs, retries, acceptance relaxations, commits, remote operations or additional model calls after the stop.

## Results and call accounting

| Scenario | Prepared reference controls | Model authoring / full generated stub gate | Generated live cases | Runtime wrapper attempts / completions |
|---|---|---|---|---:|
| SC-01 dual-ledger-closeout | Pass: 12 native executions, including rejection controls; nop rejected | 2/2 accepted, 24 native executions | 2/2 accepted | 0 / 0 |
| SC-02 support-review-packet | Pass: 10 native executions, including rejection controls; nop rejected | 2/2 accepted, 20 native executions | 2/2 accepted | 4 / 4 |
| SC-03 bulletin-market-brief | Pass: 10 native executions, including rejection controls; nop rejected | First answer rejected by exact topology gate after successful n8n execution; second not run | Not run | 0 / 0 |
| SC-04 priority-support-brief | Pass: 8 native executions, including rejection controls; nop rejected; outside stopped paid series | Not run | Not run | 0 / 0 |

The series spent **5 of 8 authoring reservations** and **4 of 17 runtime reservations**. All five authoring calls returned submitted YAML with observed `gpt-6-astra` metadata and no recognized tool-use markers. Runtime evidence correlates four append-before-dispatch attempts, four wrapper/model completions and four native Agency executions. Unused 3 authoring and 13 runtime slots do not authorize resuming the failed series. Provider-internal requests, retries and currency costs remain unknown.

There were **176 real n8n executions** in total: 35 original-three controls, 4 topology probes, 88 selected reference controls (including 48 additional fake-HTTP transport probes), 45 generated stub executions, and 4 generated live cases. Counts exclude duplicate artifact copies and do not label deterministic fake-HTTP calls as model calls. See [final execution accounting](final-execution-accounting.json).

## Independent checks

- The frozen implementation passed 109 local tests, author-reported Ruff/format and mypy checks, and the original-three Harbor oracle/nop regression. Source-matched controls used pinned n8n 2.41.5 and Harbor 0.21.0.
- Four additional actual n8n probes accepted renamed/reordered dual-ledger and priority-normal graphs, and rejected removed terminal/skipped-branch tokens. Native failure and Result presence are recorded separately in [topology evidence](topology/topology-summary.json).
- An extracted wheel compiled the baseline invoice, dual-ledger and priority workflows outside the checkout without importing verification files. See [installation check](installation-check/summary.json).
- [Spec review](SPEC.md) and [independent Standards review](STANDARDS.md) approved the same executed source manifest before calls. Concrete admission and failure-preservation findings were fixed before that freeze.

These checks establish the prepared bounded scenarios and observed executions. They do not establish arbitrary graph generalization, secure hidden-task isolation, natural-language semantic completeness or team-pilot readiness. No live SC-03/SC-04 text exists for human review in this series.

## SC-03 failure and correction

The model used `summarize → preview → product`, reading product material from `preview.text`. The planned verifier required `summarize → preview` and `summarize → product` directly, with preview and writer as separate terminal steps. The registered preview operation preserves the summary text and article IDs. In the actual native result, those values, product evidence, marketing source and final evidence order were unchanged.

The original author-visible prompt did not disclose the exact direct edge or terminal requirement. The verifier faithfully applied the internal planned graph, but that graph overconstrained the visible task. The failed trial remains rejected; this diagnosis neither retroactively accepts it nor proves its unrun fixtures would pass. See the [independent diagnosis](sc03-authoring-rejection-diagnosis.md) and [native data comparison](sc03-native-data-diagnostic.json).

After all execution ended, six public task/document files were corrected. SC-03 now explicitly declares its six edges, direct summarizer source and two terminals. SC-04 declares its nine edges, four classification guards and sole writer terminal. Both forbid extra or redundant dependencies while allowing arbitrary step IDs and declaration order. Documentation now describes these as constrained topology tasks. A separate research question records semantic equivalence versus exact-edge matching. No verifier, compiler, runtime, catalog, profile, fixture or test bytes changed.

Affected unpaid checks comprise three existing author packaging tests plus independent canonical rendering of all seven task packages. Only SC-03 and SC-04 rendered prompt hashes changed; the other five stayed identical. The old SC-03 rendering exactly matches the prompt actually sent in the failed trial. No corrected prompt was sent to a model. A fresh independent reviewer [approved the final wording](prompt-correction/REVIEW.md) at the corrected manifest, with zero blockers.

| Complete rendered prompt | Executed/before SHA-256 | Corrected SHA-256 |
|---|---|---|
| SC-03 | `e4518496b7ed3ffa8ad3dbd66472ebc2c8b6571b8ab2c1d90fd49571efecb90e` | `3a4417f5cfde766899eb1f73b382c21832fb8c97e7c1f6cbc40eefa19b587538` |
| SC-04 | `b8d89e7b3e87b467952ab097dd7c4b95e66273393f47c5865ebaa626df2e481e` | `81e4faa2b71ebea4c02a4f7b2f29995ee70c16f8364425ee0c77eb32ce3b3ef6` |

See [rendered prompt comparison](prompt-correction/comparison.json) and [correction preservation proof](prompt-correction/final-preservation.json).

## Source identity and preservation

The executed 109-file manifest is [final-source-manifest.json](final-source-manifest.json), SHA-256 `b502ffd98e9694e319b003d0ad2794c840be095540d1a75d873f40a490db1a96`; exact source bytes are archived under `executed-source/`. The corrected current 109-file manifest is [corrected-source-manifest.json](corrected-source-manifest.json), SHA-256 `f2aa719789f3dce7f5570cab9f42098edd389858d86011497f19d3e8cb9e9437`. Their only differences are the six task/document files listed in the preservation proof. All native/model evidence belongs to the executed manifest, not to a fresh model evaluation of the corrected prompts.

All 3,055 newly executed raw artifacts and 5,659 pre-existing historical files remain unchanged. The pinned specification, original configurations, wrapper source/config identities, catalog/profile and compiler are preserved. All twelve original container IDs, images, configuration hashes, states and mount sets match the actual initial snapshot; five original services were already running when this integration began. No original service was started or stopped, and no additional container remains. See [final container preservation](final-container-preservation.json). Earlier private comparison diagnostics used unstable mount-list ordering or different JSON serialization; the final comparison uses the original hash format and exact mount sets.

## Next step

Prepare a separately admitted, source-matched evaluation of the corrected constrained-topology prompts, with an explicit cohort and fresh call budget. The existing failed ledger cannot be resumed at SC-03, and no unsupported resume command is implied. Keep semantic-equivalent graph acceptance as a separately designed research change. A new paid evaluation and later human text review remain outstanding; the present turn ends with corrected usable tasks and an honestly partial model result.
