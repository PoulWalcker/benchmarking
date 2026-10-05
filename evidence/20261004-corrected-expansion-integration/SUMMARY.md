# Corrected SC-03 / SC-04 authoring and live evaluation

The fresh, separately budgeted series passed all automatic gates: **4/4 one-shot model-authored YAML submissions**, followed by **4/4 live cases** using the chronological-first submission for each scenario. It consumed exactly **4 authoring calls and 13 runtime wrapper attempts/completions**. No repairs, retries, additional generation, or SC-01/SC-02 reruns occurred. The failed historical series and all its evidence remain unchanged.

Human qualitative review of generated text is **pending**. The concrete source/output packet is [HUMAN-REVIEW.md](HUMAN-REVIEW.md). This result establishes a bounded automatic evaluation, not team-pilot readiness, full promotion, or arbitrary-workflow competence.

## Execution and results

| Scenario | Authoring + generated stub gate | Selected original YAML SHA-256 | Live cases | Actual runtime attempts/completions |
|---|---:|---|---|---:|
| SC-03 `bulletin-market-brief` | 2/2 accepted | `a9e11cbc3c7649c5d2cd6c5824f0bf21dd04c0c24027ff6193779f0f8ac7691b` | `base-bulletins`, `alternate-bulletins`: both accepted | 4 + 4 = 8 |
| SC-04 `priority-support-brief` | 2/2 accepted | `4542af50e2ea3891c5f4272123787ac61bc1165eb942e9e755e74527754f16ad` | `high-brief`, `normal-empty`: both accepted | 4 + 1 = 5 |

Both generation reports record two successful one-shot `gpt-6-astra` calls, zero repairs and no observed tool markers. Tool isolation is a prompt restriction plus stderr audit, not an enforced wrapper sandbox. Every original answer was independently checked against its own preserved verifier submission bytes. Selection used actual generation timestamps; the later accepted answer was not substituted. Both selected originals were copied byte-for-byte into live packages. Private fixture inputs and execution deadlines were the recorded live overlays.

The normal-priority SC-04 case made only its classifier call: all four research occurrences were skipped, no research outputs existed, and the final `brief` was null. The high-priority case exercised the research join and final writer. SC-03 retained the direct summary-to-product input and both preview/writer terminals required by the corrected task.

All live reports finalized `status: passed`, `source_unchanged: true`, and empty `not_run`. The durable fresh ledger contains eight passed events (four authoring reservations and four case reservations); no failed or unresolved event remains.

## Gates and native evidence

The existing drivers ran fresh selected-scenario controls before admitting any authoring for that scenario. Each included 114 passing local tests, a pinned n8n 2.41.5 image, real HTTP transport/rejection probes, reference oracle positive/negative cases, a successful-engine mutated-workflow rejection, invalid-definition rejection, and a nop rejection. These were required driver controls, not extra model attempts.

There are **82 distinct fresh native n8n executions**, including deliberately rejected executions. The inventory deduplicates 36 archived copies by `(workflowId, execution id, startedAt)` and verifies their metadata hashes agree; copies are not counted as extra executions.

| Phase | Fake-provider transport | Prepared reference native runs | Model-authored stub native runs | Live native runs | Total |
|---|---:|---:|---:|---:|---:|
| SC-03 | 12 | 10 | 20 (10 per answer) | 2 | 44 |
| SC-04 | 12 | 8 | 16 (8 per answer) | 2 | 38 |
| Total | 24 | 18 | 36 | 4 | 82 |

The reference and each generated submission faced 13 SC-03 or 11 SC-04 acceptance cases; three invalid definitions per batch reject before native execution. Positive cases, expected negative outcomes, native graph/branch/join/Result provenance, and deliberate verifier-corruption rejections are separately recorded. Nop correctly rejects the absence of a submission.

The 13 runtime calls are established by native Agency nodes plus independent three-event audit reconciliation, not HTTP status alone. Each unique invocation binds an append-before-dispatch reservation to a successful unchanged-wrapper completion and the native response, with recomputed input, prompt, request and response hashes. Native Prepare/Guard/Agency/Restore records, persisted execution data, workflow/config correspondence and final Result values agree. There are exactly 13 native HTTP attempts, 13 outgoing runtime wrapper attempts, 13 successful wrapper completions and 13 correlated Agency replies; no observed retry marker is present. Provider-internal requests, retries and monetary cost remain unknown.

## Frozen admission and source

The previous all-four-scenario admission could not safely start at SC-03. A minimal generic subset change was independently reviewed before dispatch. It changes only:

- `src/sapi_config_lab/experiments/expansion.py`
- `src/sapi_config_lab/experiments/generation/run.py`
- `src/sapi_config_lab/experiments/live.py`
- `tests/test_expansion_runner.py`
- `docs/SCENARIO-EXPANSION.md`

The v2 ledger freezes a canonical selected cohort and derives ceilings. Both generation/live CLIs carry the same two repeated `--series-scenario` flags. Unknown, duplicate or reordered cohorts, changed selection, legacy-ledger migration, unknown/failed attempts, changed source and missing/failed prior selected-scenario final reports are rejected. The default remains the full four-scenario cohort. The subset author ran 27 focused tests plus Ruff and mypy; independent review approved the concrete empty ledger and commands before dispatch.

- Baseline corrected-task manifest: `f2aa719789f3dce7f5570cab9f42098edd389858d86011497f19d3e8cb9e9437`.
- Executed/current 109-file source manifest: **`265d4fd30ed46028acaf898c3f168d6bea0a732e926d80659ae4fbd5d1850f5b`**, stored as [executed-source-manifest.json](executed-source-manifest.json), with exact bytes in `executed-source/`.
- SC-03 rendered prompt: `3a4417f5cfde766899eb1f73b382c21832fb8c97e7c1f6cbc40eefa19b587538`.
- SC-04 rendered prompt: `81e4faa2b71ebea4c02a4f7b2f29995ee70c16f8364425ee0c77eb32ce3b3ef6`.
- Initial fresh v2 ledger hash: `2651e5eec55cc78932a264ae7072b65dad75879d6fdec7a7812cd5afb7626aa3`, selecting only SC-03/SC-04 with ceilings 4/13, zero events and `failed: false`.

Rendered prompts were frozen before private fixture generation. Every generation and selected replay pins the fixture corpus hashes. The same finite operation catalogue, backend/compiler, acceptance checks, profile, shared FORMAT and pinned upstream spec `06ddd3333109cea8a2cb3071609070d7a3c0d3ff` were preserved. The public tasks explicitly prescribe topology; semantically equivalent alternative edge layouts are outside this benchmark's acceptance contract. This is not evidence of general composition beyond the catalogue or arbitrary workflow semantics.

## Preservation and review

[PRESERVATION.json](PRESERVATION.json) passes: all 109 current and archived source hashes, all 9,150 pre-existing historical file hashes, unchanged wrapper/config, and all 12 original container identities/configurations/images/mounts/states match the baseline. Five original containers were running both before and after; none was started/stopped/restarted by this task. No extra container remains. Container mounts are compared independent of ordering.

The initial diagnostic incorrectly serialized an absent Docker bind-mount `Name` differently from baseline `Name: null`. Its three representational mismatches are preserved in `PRESERVATION-initial.json`; the final assessment normalizes that optional field using the saved raw captures. There was no service action or runtime rerun for this correction. No commit, push, remote modification, workflow import into existing services, or wrapper edit occurred.

Independent review is in [independent-review.md](independent-review.md), covering source/admission, both authoring selections, and raw native/wrapper evidence. Human text review is still pending and is explicitly separate from those technical reviews.

## Reproduction and evidence index

[planned-commands.json](planned-commands.json) contains the exact six ordered argv entries and working directory used. Its report paths and ledger are now completed evidence, **not a resume command**. Any further paid batch requires a new separately admitted ledger and new output paths; unused/repeated historical grants must not be reused.

- [SC-03 generation report](sc03-generation/report.json), [selection](sc03-selection.json), [live report](sc03-live/report.json).
- [SC-04 generation report](sc04-generation/report.json), [selection](sc04-selection.json), [live report](sc04-live/report.json).
- [Final series ledger](series/series.json), [initial ledger](initial-series-ledger.json), [preflight freeze](preflight-freeze.json), [prompt hashes](frozen-prompt-hashes.json).
- [Native execution inventory](native-execution-inventory.json), per-case `case-audits/`, and each report's preserved Harbor `jobs/` trees.
- [Wrapper identity](wrapper-identity.json), baseline/final container identity captures, historical hashes and exact source archives.

Next useful step: review the concrete text in HUMAN-REVIEW.md against its fixtures and record the human judgment. The planned third independent authoring attempt per task and any broader semantic-equivalence work remain separate future evidence; this two-attempt series does not claim team-pilot readiness or full promotion.
