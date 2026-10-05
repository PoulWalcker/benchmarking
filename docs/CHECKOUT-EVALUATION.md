# Checkout recovery evaluation

Run one case from a plain request to two independent generated YAML submissions,
a frozen selected workflow, real n8n tool actions, and the original independent
AutoWFBench rubric:

Prerequisites: uv, Docker with Compose, an authenticated Codex CLI, and the
existing inspected wrapper at `http://127.0.0.1:8765/run` (source at
`~/n8n/codex_bridge.py`). The wrapper uses the model already configured in
`~/.codex/config.toml`; the harness does not change it. First install the locked
extras and fetch the public, hash-verified source dependency:

```bash
uv sync --locked --extra harbor --extra benchmark
uv run --locked --extra benchmark python -c 'from pathlib import Path; from sapi_config_lab.runtime.autowfbench import fetch_source; fetch_source(Path(".cache/autowfbench"))'
```

Then run the complete evaluation with one command:

```bash
uv run --locked --extra harbor --extra benchmark sapi-lab checkout --mode live --report-dir reports/checkout-new-run
```

The command builds the pinned n8n image, runs unpaid source/image-matched
reference/nop controls, then performs bounded live authoring, runtime, and judging.
No manual graph import or editing is needed. See
[AUTOWFBENCH-ENVIRONMENT.md](AUTOWFBENCH-ENVIRONMENT.md) for cache verification.
Existing reports are immutable; use a new directory. `--mode prepare` writes the frozen prompt,
contract, wrapper identity, source manifest, and budget without model calls.
`--mode controls` runs scripted reference/nop through Harbor and real n8n with an
explicit **simulated** judge; its reward is transport calibration, not a model score.

The live command allows two authoring calls, selects the chronological first
eligible unedited YAML, permits at most four runtime Agency calls, and calls the
original independent Codex judge once each for reference and candidate: at most
eight model dispatches. There is no repair, feedback-driven selection, retry, or
post-hoc rubric change. Failed attempts remain in the report. CLI metadata records
requested and observed model identity where available; provider-internal retry
counts and monetary cost are not established.

This evaluates generated, prebuilt YAML as a workflow solution. Authoring is a
separate phase, recorded with its duration and failure rate. The original
120-second runtime begins after environment provisioning and before compilation
and real n8n execution. A timer freezes simulator evidence at expiry. The full
pipeline time also includes authoring, provisioning, and judging. It is not a
claim of equivalence to an agent that authors code inside its original timed run.

The simulator is the pinned original constrained AST checkout-patch environment,
not a production checkout repair. Four original tools are exposed to native n8n
HTTP nodes. Tool replies are preserved as JSON strings; the compiler has no
checkout patch, business criteria, or scenario-specific graph. Reference patches
live only in trusted test support, outside the candidate image and author prompt.
The candidate graph receives an ephemeral candidate-only token at execution;
compiled artifacts use an environment expression rather than a literal token.

Reports retain the original four deterministic criteria worth six points and
three semantic criteria worth four points,
per-criterion evidence, execution_pass, score_0_10, and Harbor reward=score/10.
A missing, invalid, or failed judge produces no reward, not zero. Task score and
runtime success are separate; a valid low score is a completed measurement.
`incident-summary.md` naming is an additional labelled diagnostic only and does
not alter the upstream score.

The authoring wrapper remains unchanged and runs outside this repository. No-tool
instructions and recognized stderr-marker rejection are observational controls,
not enforced tool isolation. Runtime Agency uses the same marker rejection for
this benchmark. The verifier and candidate share a container; this is functional
separation for schema-constrained YAML, not an adversarial arbitrary-code sandbox.
The independent judge has its own prompt and subprocess; prompt-injection
robustness against candidate text has not been established. No existing n8n UI
workflow, account, database, container, or shared wrapper configuration is changed.

## Recorded first case

On 2026-10-05, both independent authorings were eligible. The chronological first
was executed without editing: eight logical steps, six simulator tool calls, and
two runtime Agency calls. The original independent judge awarded **9.33/10**;
Harbor recorded **0.933**, and `execution_pass` was true. Deterministic criteria
received 6/6 points; semantic criteria received 3.33/4. Communication received
partial credit because unresolved deployment/recovery/impact limitations lacked
concrete next steps. The frozen result was not repaired or regraded.

Runtime was 63.73 seconds under the original 120-second limit. The full measured
live pipeline took 301.70 seconds. Actual model dispatches were six: two authors,
two runtime operations, and two independent judges (reference and candidate).
The scripted reference was reported separately and also received 9.33/10.
Only one selected candidate and one seed were evaluated; this does not establish
performance across the benchmark or on production incidents.

Private evidence is in `reports/checkout-live-20261005/`: `report.json`, the exact
selected `authoring-1/submission.yaml`, `candidate/run-log.json`,
`candidate/evaluation/evaluation.json`, and the native Harbor execution beneath
`candidate/jobs/`. The native `workflow.json` uses a token-free environment
expression; it is an execution artifact, and its temporary endpoints require a
fresh harness run. The earlier controls a/b/c are development evidence; final
source-matched unpaid controls are `reports/checkout-controls-20261005-d/`.

Independent scoring and environment/transport audits matched receipts, hashes,
reward arithmetic, and six actual calls. Validation passed 228 local tests,
Ruff, mypy, and the distribution privacy check. Exact-token scans found no
persisted candidate/verifier token. All 15 existing UI workflow fingerprints and
existing container identities were unchanged; execution database counts were
not inspected. Trial containers and temporary environments were removed; the
reusable owned image was retained. This documentation update followed the frozen
run and did not change its source manifest or evidence.
