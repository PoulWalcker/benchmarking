# Recorded experiment results

These selected outcomes summarize locally preserved historical evidence from
October 4, 2026. Raw prompts, model answers, logs, execution records, task-package
copies, and review dumps are not public source files. They remain unchanged in
the original local `reports/` directories, outside Git and distributions.

All series use the specification pinned at
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`; the public
[specification comparison](../provenance/spec-comparison.json) records its hash.
The tests cover the limited `sapi-lab/v0` profile, not the complete specification.

| Series | Recorded outcome | Scope |
| --- | --- | --- |
| Prepared YAML, deterministic operations | 12/12 transport probes; 3/3 Harbor oracle rewards 1; 3/3 nop rewards 0 | 32 verifier cases: 23 real n8n executions and nine expected compile rejections |
| Prepared YAML, live operations | Three accepted scenarios, seven verifier cases, eight successful model calls | A previous failed quotation check was preserved; the prompt was manually clarified before the passing series |
| Model-authored YAML | Nine accepted attempts across three known task families | 69 real n8n execution records and 27 expected compile rejections; runtime LLM operations used stubs |
| Initial package reorganization | 42 local tests; 12 transport probes; oracle/nop controls passed | 24 compilation comparisons were identical; historical evidence remained unchanged |
| Frozen model-authored YAML, live replay | Seven accepted cases; eight runtime wrapper completions; zero authoring calls | Immutable overall report remains failed on its preservation gate; the user-confirmed service stop is recorded separately |
| Four expansion task families | Eight selected-artifact live cases passed with 17 runtime wrapper completions across two series | Original SC-01/02 and corrected SC-03/04 each used two accepted one-shot submissions per task; the first series' SC-03 rejection remains preserved |
| Bounded reply refinement (04) | Three one-shot YAMLs accepted; selected original used four runtime calls | Positive accepted its first draft; impossible-limit case exhausted three real feedback attempts with no accepted output. Rejection then acceptance was observed only in native stub controls |
| Authored digest lifecycle (05) | Seven wrapper calls: one one-shot authoring, five native digest calls and one WBS model repair | Unchanged authored revision passed Callback/actual-clock Cron. Separate deliberate mutation was rejected; repaired revision passed new Callback/actual-clock Cron. External controller and fixed-minute evaluation schedule overlays were explicit |
| Two composition tasks (G1/G2) | Four untouched one-shot YAMLs and four selected native live cases passed; 12 runtime calls under a 14-call cap | Both model answers per task used the same shape. G2 shared product analysis with inline writer inputs, adding a third accepted shape beyond its two manual native controls. Catalog and compiler were unchanged |
| Local n8n UI | Manual execution 61 passed with four correlated runtime calls | New inactive workflow; six pre-existing workflow hashes preserved; foreground helper stopped |
| Pinned-source maintenance | Recorded upstream fetch equals vendored bytes; pin unchanged | Offline verification and explicit fetch/diff are available; this does not adopt a newer specification |

These are historical checks of the sources captured in each run. They do not
certify later source or prompt edits. In particular, translating current prompt
documents changes future prompt hashes. The [two composition experiments](GENERALIZATION-EVALUATION.md)
evaluate operations, bindings, reuse and output structure within the existing
catalog. Their automatic prose checks are lexical, supplemented by an independent
agent read of the live outputs. No Sapiens executor, arbitrary workflow generation,
or team-pilot readiness is established by these bounded results.

The [README evidence table](../README.md#evidence-and-limits) links the individual
series and usage guides. [Independent agent review](../../evidence/20261004-open-tasks-integration/INDEPENDENT-REVIEW.md)
separates technical acceptance from prose quality. SC-02's correctly rejected
support drafts are not accepted customer answers; human review is not claimed.
For 05, the independent review recomputed the WBS candidate, prompt, source and
findings hashes. The full raw wrapper envelope and stderr were not retained;
their hashes remain recorded metadata, not independently recomputed evidence.

## Historical report identifiers

The paths below identify local evidence; they are deliberately plain text, not
links required by the public documentation. SHA-256 identifies the exact bytes
summarized above and is not a substitute for the underlying evidence.

| Local path | SHA-256 |
| --- | --- |
| `reports/20261004-verified-2/report.json` | `f31615dc6b36f7a581e72f3ffc60824b04d3279500df2e92a54df642498df34b` |
| `reports/20261004-verified-2/live/report.json` | `4f22e9aa58f8a0c0507b8810ff646bf1ef5125b4a5e47cecd4c2933239926a61` |
| `reports/20261004-yaml-generation-1/report.json` | `9858951f9111990ee244be3a1edaf562304d77c090eb8fbf5a00053f8bb45bc4` |
| `reports/20261004-refactor-final/report.json` | `7848071b73961ab3454cf4d2afb6d3adc6475b930a8e4fed3a3f9e8ea0674d35` |
| `reports/20261004-english-docs/report.json` | `24fb5c8adbe2d32c30f2dc3c556b2fb351f0b96d587f852ed058d70f379b973b` |
| `reports/20261004-generated-live-integration/live/report.json` | `bcd9aada49503563ee97518d8a03a202309d5971aa4551b756fcd3052b3388b2` |
| `reports/20261004-scenario-expansion-integration/sc01-live/report.json` | `f4a4e85922fab78294f9e340d23e0a51691e4257ea139842691e90e9d18ba1ba` |
| `reports/20261004-scenario-expansion-integration/sc02-live/report.json` | `f0e8d09b9fef2608085eda1ce2157590915b91a6d88ecf89a8e4f0e5230c96ae` |
| `reports/20261004-corrected-expansion-integration/sc03-live/report.json` | `1de2f20933a1e70d2511cb69e5554bcb3779b826f5ac6373979fcb2ad37c7945` |
| `reports/20261004-corrected-expansion-integration/sc04-live/report.json` | `bc86fba91f3fb409b68a0aaf8d434ab960a8bbcfab4733642acce94f020c18e3` |
| `reports/20261004-open-tasks-integration/refinement-evaluation-2/live/report.json` | `e72d2ac1dbef81f126f41836c3aea7fd35b8b797864e13c73a8216e0f33e674e` |
| `reports/20261004-open-tasks-integration/lifecycle-evaluation-3/after-authored/authored/report.json` | `9636374bab5f4324e60b9660e58a95bec25e8c002fcb25a729a3df257a90a599` |
| `reports/20261004-open-tasks-integration/lifecycle-evaluation-3/after-mutation/mutation/report.json` | `87bc8ed4dd9cd30fdf071b2a7b62f600cf3624a450e17bec8f83b151ae45cb60` |
| `reports/20261004-open-tasks-integration/generalization-evaluation/series/billing-bulletin-packet/live/report.json` | `4d1979025192b906ed066ca9bcffab35f25da3b7993d4dad5af6ee2b513808ce` |
| `reports/20261004-open-tasks-integration/generalization-evaluation/series/two-audience-briefs/live/report.json` | `20e1cb5ea18ecfd69972a9c9d2c05a1570a5984e39748476b7d0fb5eec7a1bec` |
| `reports/20261004-open-tasks-integration/ui-demo/run/verification.json` | `8d51af51d0a06831ae707bcde05660da33f803ebb303b27372be9264dc8f7ae2` |
| `reports/20261004-open-tasks-integration/spec-pinned-comparison/comparison.json` | `050ecdf9527e6942e23216411ff8fe95d8ef50fd27179279b72bfd48a9f953fd` |

## Reproduce the current sources

Run from a fresh checkout with Node.js, uv, and Docker with Compose:

```bash
uv sync --locked --extra harbor
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
uv run --locked python -m unittest discover -s tests -v
uv run --locked python infra/check_distribution.py
./run.sh
```

The final command creates a new timestamped local directory, rebuilds the
isolated n8n image, and runs transport and Harbor controls without a live model.
Each run records host/container versions and a source manifest; changes to
public sources during the run make its overall status fail. Do not edit the
checkout until it finishes. Existing report directories are not rewritten.

Live-model experiments are explicit opt-in commands described in the
[main README](../README.md) and [generation guide](../generation/README.md).
CI does not run them. The optional Docker CI job also keeps raw artifacts local
to its runner and contains no artifact-upload step.
