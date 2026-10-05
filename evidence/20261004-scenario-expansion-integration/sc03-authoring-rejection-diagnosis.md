# SC-03 authoring rejection: read-only diagnosis

The first SC-03 submission was rejected because the frozen verifier requires the exact planned topology: product analysis must read `summarize.text` directly, and preview must remain a terminal step. The submitted workflow instead reads the same text through `digest.preview`. That requirement is explicit in the internal plan but absent from the actual authoring task, FORMAT, and catalog. The best-supported classification is **a public-task / structural-acceptance mismatch**, not a demonstrated actor-permission violation, lost source value, or incorrect business output.

The recorded rejection remains authoritative for this frozen series. This diagnosis does not turn the trial into a pass or establish that the unrun fixture/control set would pass.

## Scope and method

This is a separate diagnosis note. Original prompt, submission, config, verifier, and native records were only read. No model/provider calls, Docker execution, test/experiment reruns, YAML repairs, source edits, or acceptance changes were made. No new external research was needed.

The `ask-matt`, `diagnosing-bugs`, and `codebase-design` skills were read. The debugging skill's reproduction/minimization/fix phases were deliberately bounded to inspection of the preserved red result and a static counterexample, because this assignment explicitly prohibited additional experiment execution or implementation. No fresh red/green loop is claimed. No `.codegraph/` directory was present.

Inspected trial: `sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P`. Exact submission SHA-256: `124fda0fe69701a8b9115671a39e2bbf8bae4325e07f28e16497707b08791e97`. The preserved [verifier report, lines 9–40 and 48–53][report] records successful n8n execution, zero runtime Agency HTTP calls in stub mode, and rejection with `Missing, ambiguous, or incorrect occurrence role/input lineage`. Native workflow ID is `559a6bab21a54b7d`, execution ID `1`.

The bounded hypotheses were: (1) exact topology/input-reference rejection despite unchanged business data; (2) incorrect data or evidence introduced by preview; (3) actor, operation-count, or other visible-task violation. The preserved code and records support (1); inspection found no evidence for (2) or (3).

## Smallest concrete counterexample

All seven operation roles are unique. The relevant difference reduces to three occurrences:

| Obligation | Frozen verifier / internal plan | Exact submitted YAML |
| --- | --- | --- |
| Preview input | `steps.summarize` | `steps.summarize_bulletins` |
| Product material | `steps.summarize.text` | `steps.retain_preview.text` |
| Relevant edges | `summarize → preview`, `summarize → product` | `summarize_bulletins → retain_preview → analyze_product` |
| Terminal roles | preview and writer | writer only |

The submitted references and edges are at [submission.yaml, lines 41–51 and 71–77][submission]. The required references and edges are at [scenario_contracts.py, lines 107–126][contract]. Renaming IDs cannot reconcile this difference.

Let `S` be any valid digest summary and `D = S.text`. The inspected frozen operation is `digest.preview: ({summary}) => ({mode: 'preview', ...summary})` ([operations.js, line 60][preview-implementation]). Therefore `preview(S).text = D` and `preview(S).article_ids = S.article_ids`; the actual product material is the same `D` as the required direct material. This is a specific, proven passthrough through the registered operation, not permission to replace source lineage with coincidental value equality or a constant.

The changed terminal structure is real: the native [Result record, lines 1029–1037][result-record] has only writer as its predecessor. Consequently this submission does **not** demonstrate SC-03's planned two-terminal composition, even though its preview remains included in the result.

## Exact rejection path

`check_execution` invokes `observe_execution` before business acceptance ([verify.py, lines 36–42][verify]). `observe_execution` reaches `bind_roles` at [n8n_provenance.py, line 85][observation]. The binder first requires equality of the entire dependency edge set at [roles.py, lines 154–159][binder]. That check fails for the unique assignment: the submitted edge `preview → product` differs from required `summarize → product`.

The internal rejection is caught and discarded at binder lines 186–187, and the generic message is raised at line 191. The later exact input-origin comparison at lines 162–164 would also reject `preview.text` against `summarize.text`. Thus this is not an ambiguous role assignment, and the first recorded gate does not identify a business predicate failure. Business checks are not reached by this trial's acceptance path.

The broad binder error hides the specific mismatch. A future diagnostic improvement can retain the unique candidate's first failing invariant (here, dependencies), without changing any acceptance rule.

## What the model was actually told

The actual [prompt.txt, line 2][prompt] asks the author to retain the digest as a preview, analyze capabilities from that digest's actual text, preserve evidence, and use four separately restricted model actors. It does not require a direct summarizer reference, prohibit reading preview text, or state that preview must be terminal.

FORMAT permits any referenced producer that is an ancestor (prompt lines 42–45), explains that output resolves after terminal steps finish (lines 29–32), and does not require returned objects to originate at terminal steps. The public catalog explicitly exposes `digest.preview` outputs `mode`, `text`, and `article_ids` (lines 410–414). Its short Script declaration does not explain the passthrough implementation, but neither it nor FORMAT forbids using `preview.text`. The generated acceptance description explicitly says it will analyze “exactly the retained preview text” (submission lines 79–83), consistent with that visible reading of the task.

By contrast, the [internal plan, lines 229–245][plan] explicitly specifies `summarize → product`, `summarize.text`, and preview/writer as two terminal steps. The plan's lines 333–339 deliberately exclude reference YAML and verifier code from generation. The exact graph obligation was therefore enforced from non-author-visible material, rather than communicated as a requirement in the human task paragraph.

The verifier faithfully implements that internal plan. Calling this a generated YAML violation of an **explicit author-visible** direct-edge requirement would be inaccurate. Calling it fully accepted would also be inaccurate, because the frozen experiment deliberately specified and enforced a stronger topology.

## Preserved native evidence

Inspection of the original [case.json][case] establishes the following for the one executed base fixture; private fixture marker strings need not be reproduced here:

- The preview's text and article IDs equal the summarizer's values (lines 311–325).
- Native product execution receives the preview envelope as its predecessor (lines 368–381); its retained digest, preview text, summary, and evidence are consistent with the same digest text (lines 417–441).
- Marketing is an independent fixture root (lines 486–495); its evidence quotes its own supplied material (lines 518–527).
- The combine result preserves both complete analyses and evidence (lines 782–798).
- The final output has exactly `digest` and `brief`, preview mode, both article IDs, both named capabilities, the audience and channel, and product/marketing evidence in order (lines 3–18).
- Four distinct actors each allow only their assigned model operation in submitted YAML lines 3–15; assignments occur at lines 38, 49, 55, and 68. All seven operation occurrences complete in the preserved trace. There is no observed actor-permission defect.

These observations match the semantic obligations implemented in [scenario_business.py, lines 96–136 and 175–193][business], including unchanged preview, facts, and evidence. They are an inspection of existing evidence, not a new successful invocation of that verifier. Stub results establish data transfer here; they do not establish live model text quality. The second SC-03 authoring attempt, SC-03 live cases, SC-04 paid stages, and remaining SC-03 generated-fixture/control results are unobserved.

## Smallest next-series correction

The existing plan intentionally tests a two-terminal graph. Preserve that goal and the strict verifier; add one explicit sentence to **SC-03's human task paragraph** before freezing a separately identified series:

> Product analysis must depend directly on digest summarization and read that summarization step's text; digest preview must have no outgoing dependencies, leaving preview and the final writer as the two terminal steps.

This communicates the measured obligation without supplying step IDs or reference YAML. No common FORMAT, catalog, profile, compiler, or runtime change is needed. A regression should ensure the frozen authoring package actually contains this topology requirement; the preserved preview-backed config should remain a rejection control for the strict topology contract. Do not repair this submission or rerun it within the stopped series.

The authoritative editable task is `generation/tasks.json:7`; the prose mirror is the human-task paragraph in `docs/planning/scenario-expansion.md:210`. These are future-series changes, not changes to the copied prompt or instruction under this report. While the integrator's separately authorized unpaid SC-04 control uses frozen sources, source/document edits must wait for release of that freeze.

If the intended task is instead broadened to accept any business-equivalent composition, that is an explicit acceptance-policy change for a new series. At the existing role-contract/binder seam, the minimal generic mechanism would be a finite set of declared admissible lineage variants, not removal of origin checks: one direct-source variant and one proven preview-passthrough variant. Retain exact operation counts, exclusive actors, guards, output bindings, native edge/join evidence, and unchanged preview/content checks. A replay regression from these preserved bytes should accept both legitimate variants under that new policy, while rejecting constants, wrong source references, modified preview text, and lost evidence. Such a policy would no longer require SC-03's two-terminal topology and must be reported that way. It is not justified as an in-series fix merely to obtain a pass.

## SC-04 disclosure cross-check

The public task at `generation/tasks.json:8` already requires action selection before the independent analyses, a combine then writer, and the unconditional classification decision as the guard on every optional-region step. It therefore discloses its main semantics more clearly than SC-03. The frozen contract nevertheless requires an exact edge set (`scenario_contracts.py:128–160`), and the public wording does not explicitly prohibit redundant dependencies. For example, adding a direct classification-to-writer edge and the required `all_terminal` join preserves the stated guard, source values, and ordering but is rejected by the same exact-edge predicate. This is a static counterexample, not an executed variant.

Before a new authoring series, disclose the tested graph in SC-04's task paragraph and its plan mirror: use one occurrence of each of its eight operations; the only dependencies are classification to each action draft, both drafts to selection, selection to each independent analysis, both analyses to combination, and combination to the writer. Writer is the sole terminal step. The existing FORMAT already describes joins, ancestor references, and optional references; it need not change. For SC-03, likewise identify its six intended role edges as the only dependencies if exact edge-set equality is meant to remain part of the author-visible obligation. This is task-specific acceptance disclosure, not a new compiler restriction or an in-series acceptance relaxation.

## Final wording review after the authorized correction

**Approved; no actionable wording blocker.** The independently checked corrected 109-file manifest has SHA-256 `f2aa719789f3dce7f5570cab9f42098edd389858d86011497f19d3e8cb9e9437`. All current file hashes match it. Exactly six authorized task/document paths differ from `executed-source`: `generation/tasks.json`, both SC-03/SC-04 Harbor instructions, the plan, `docs/SCENARIO-EXPANSION.md`, and `docs/RESEARCH-QUESTIONS.md`.

The actual rendered prompts under `prompt-correction/after-packages` match the authoritative task strings. SC-03 discloses seven occurrences, all six exact edges, the direct summarizer-text reference, and preview/writer as its only terminals. SC-04 discloses eight occurrences, all nine exact edges, all four classification-priority-equals-high research guards, and writer as its sole terminal. Both forbid redundant edges while retaining arbitrary step IDs and list order. The Harbor and plan task mirrors match after whitespace normalization. The rendered FORMAT/profile/catalog sections are unchanged. Scope documentation explicitly limits acceptance to the prescribed topology, and the research follow-up does not relax the contract or reinterpret the stopped series.

Reviewed rendered prompt hashes: SC-03 `3a4417f5cfde766899eb1f73b382c21832fb8c97e7c1f6cbc40eefa19b587538`; SC-04 `81e4faa2b71ebea4c02a4f7b2f29995ee70c16f8364425ee0c77eb32ce3b3ef6`. This review read files and compared hashes/text only; it made no model calls, test runs, source edits, or changes to the original failed trial. The diagnostic links below continue to identify the original evidence, including the archived pre-correction plan.

[report]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P/verifier/report.json:9
[submission]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P/agent/submission.yaml:41
[contract]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/task-packages/bulletin-market-brief/tests/scenario_contracts.py:107
[preview-implementation]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/baseline-source/src/sapi_config_lab/runtime/n8n/operations.js:60
[result-record]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P/verifier/cases/base-bulletins/case.json:1029
[verify]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/task-packages/bulletin-market-brief/tests/verify.py:36
[observation]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/task-packages/bulletin-market-brief/tests/n8n_provenance.py:85
[binder]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/task-packages/bulletin-market-brief/tests/roles.py:154
[prompt]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P/agent/prompt.txt:2
[plan]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/executed-source/docs/planning/scenario-expansion.md:229
[case]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/jobs/generated/bulletin-market-brief__AMHwS4P/verifier/cases/base-bulletins/case.json:311
[business]: /Users/pashvel/Desktop/sapi-config-lab/reports/20261004-scenario-expansion-integration/sc03-generation/task-packages/bulletin-market-brief/tests/scenario_business.py:175
