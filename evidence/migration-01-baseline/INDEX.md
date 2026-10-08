# Ticket 01: frozen migration baseline

Baseline revision: `26e1d64b83a072e10b145e58000f1a0242ff3e61` (the audit revision).
The original checkout was clean; there is no revision delta to infer away.
Working branch: `migration/01-freeze-baseline`; worktree: `/private/tmp/sapi-baseline-01`.
Scope is ticket 01 only. No runtime/compiler/benchmark changes, legacy deletion,
Harbor upgrade, prompt change or paid model/judge dispatch is part of this capture.
The hosted controls use the existing simulated judge; their scores are control
observations, not a claim of externally judged narrative quality.

Control and final verification status is recorded in `verification.json` and
`controls.json`. Raw local output lives in
`/private/tmp/sapi-baseline-01/reports/migration-01/`; the control report is
`all-eleven/report.json`. Do not use this snapshot as a spending gate for changed
sources or images. The controls run against the unchanged audited sources before
the test/documentation changes in this ticket; the final check covers those changes.

## Artifact index and reuse

| Artifact | Meaning |
| --- | --- |
| `source-manifest.json` | Every experiment source hash before controls; exactly the audited checkout |
| `input-hashes.json` | Exact generation, benchmark, provenance, catalog, operations, agency prompt and lockfile bytes |
| `prompts/` and `benchmarks.json` | Actual generated full/scenario prompts and catalog bytes, per-file SHA256, manifests, cases, budgets, resolved Harbor settings; hosted tasks have only the full arm |
| `plans/` | All nine fixture observation plans, including reference-applied inputs and invalid/corrupted-artifact probes |
| `fresh-fixtures/` | One captured freshness expansion for each of four composed benchmarks; generated values may vary, obligations may not |
| `contracts/` | Both pinned hosted source/task/judge contracts, including upstream package hashes |
| `bounds.json` | Resolved verifier, job and suite ceilings |
| `cli.json` | Actual `--help` stdout/stderr, exit and argv for root and every public/internal CLI command |
| `host.json`, `planning-inputs.json` | Interpreter/dependency identities and SHA256 of the supplied ignored audit/spec/index/ticket |
| `legacy-imports.json` | The 25 existing source/target ownership edges; historical exceptions, never permission for new callers |
| `historical-files.json` | All 78 pre-existing committed JSON artifacts: path, SHA256, size, top-level keys and schema where present |
| `historical/index.json`, `historical/*.tar.gz` | Two actual old records preserved byte-for-byte, original locations and individual file hashes |
| `controls.json`, `control-report.json`, `transport-summary.json` | All 22 observed results and authoritative record paths, raw report and real transport observations |
| `verification.json`, `logs/` | Exact verification commands, exits and complete logs |
| `artifacts.json` | Raw run file hashes plus preserved local archive location/hash; no changes to original evidence |
| `SHA256SUMS.json` | Sealed hashes of this evidence directory, excluding itself |

For another checkout, use the versioned extracts and historical archives here.
For full native records, use the raw run/archive named by `artifacts.json`; verify
its hash before extracting into a new directory. Absolute paths in original
reports remain original observations. Resolve them through the recorded run root
instead of rewriting report/evidence bytes. The original revision is recoverable
from Git; `input-hashes.json` identifies exactly which bytes must be preserved.

All 22 controls passed with both Harbor job exits `0`. Both hosted oracle rewards
were `0.732`; both hosted nop rewards were absent with exactly
`RewardFileNotFoundError` and null quality scores. No paid calls were made.
Fixture oracle `revise-answer` has normalized execution `false` despite acceptance
`true` because its expected exhaustion case fails native execution; `daily-digest`
has normalized execution `null` because lifecycle rows are not ordinary positive
case rows. Preserve these observations instead of coercing them to success.

## Semantic comparison rules

Every fixture oracle must be accepted with Harbor reward `1`, no exception and
control-job exit `0`. Its negative inputs/probes must be rejected as specified by
the plan; an expected refinement exhaustion is a valid positive-case outcome.
Every fixture nop must have reward `0`, no exception, acceptance false and job exit
`0`; absent execution/quality stays null. Review per-case execution separately
from acceptance. Rubric score does not replace the fixture binary reward.

Both hosted oracles must pass independent acceptance and their declared reference
reward (`controls.reference_reward` in the captured manifest). Preserve separately
the native engine result, timely terminal completion and upstream acceptance.
Legacy AutoWFBench `result.execution` means terminal completion. Hosted nop must
fail acceptance, retain unscored quality with null score/reward and have no numeric
Harbor reward. Only the established `RewardFileNotFoundError` is permitted for
that unscored control; it is not permission for other exceptions or a failed job
exit. The separate deterministic-provider contract with quality `null` requires
reward `0` and no exception, as covered by existing provider/control tests; neither
of the two real hosted benchmarks uses that deterministic contract.

Permitted comparison differences are explicitly limited to wall-clock timestamps,
durations caused by host overhead, generated run/trial/event/session identifiers,
temporary/job/checkout paths and fresh fixture nonce/value substitutions that
preserve the captured freshness contract. Preserve identifier relationships,
ordering, timing relative to deadlines, case/probe membership, operation and call
counts, budgets, native status, terminal reasons, acceptance, quality nulls, score
and reward. Do not normalize missing evidence or source mismatches. Hashes of new
sources/packages/images may legitimately change after ownership moves but require
fresh matching controls; exact prompt/catalog bytes remain fixed. No raw evidence
normalization was performed in this ticket.

## Deadline inventory (audited implementation)

Configured values are ceilings, not measured durations. Resolved values per
benchmark are in `benchmarks.json`; observed Harbor/trial timing is in the raw
results referenced by `controls.json`.

| Boundary and timer start | Current limit / semantics | Source at baseline |
| --- | --- | --- |
| Native version subprocess | 30 s from subprocess launch | `execute/n8n.py:version` |
| Native import, then execute subprocess | Import 180 s; execution `max(180, declared_deadline + 90)` from each subprocess launch. An absolute binding caps each by remaining time, including import | `execute/n8n.py:execute_compiled`, `execution_ceiling` |
| Ordinary unbound fixture DAG | No `deadline_at_ms` initialized in Fixture; subprocess ceiling and configured HTTP limits apply. Do not retrospectively claim an absolute wall-clock deadline at workflow start | `compile/n8n.py:compile_n8n`, Fixture initialization |
| Refinement | `Date.now() + deadline` at the first Fixture; same deadline across attempts, capped by binding when present; stop on accepted attempt or exhaust bounded attempts | `compile/refinement.py`, `runtime-fragment.js:checkDeadline` |
| Workflow lifecycle | Absolute deadline set when an event is reserved/admitted, before execution/import; recorded unchanged in reservation and native evidence | `coordinate/lifecycle.py`, `verification/lifecycle.py` |
| Hosted trial | Session startup completes, then `TrialHost.begin` starts monotonic elapsed clock and deadline timer; wall-clock deadline passed to worker, 120 s for both actual tasks | `execute/hosting.py:begin`, frozen upstream definition |
| Terminal outcome | Elapsed captured before world finalization; late/invalid completion is separate from native success; lock admits one terminal writer | `execute/hosting.py:_record_terminal`, `terminal_submission` |
| Worker RPC | `/begin` 240 s; `/finish` 240 + evaluator 375 s from request | `coordinate/hosted_worker.py` |
| Hosted environment startup and RPC | Readiness 15 s; host-to-world tool request 15 s; candidate socket 15 s | `execute/autowfbench.py` |
| Compiled HTTP call | Default request ceiling 190 s; ordinary calls capped by declared workflow deadline; LLM bridge capped at 185 s and remaining envelope deadline where bound | `compile/n8n.py`, `compile/refinement.py` |
| Agency dispatch | At most 185 s, also capped by grant expiry before dispatch; timeout is unknown outcome, not a released reservation | `execute/agency.py` |
| Author wrapper | HTTP request 195 s; no calls made here | `author/agent.py` |
| Evaluator | Upstream worker 30 s, judge 180 s; aggregate `5*30 + 180 + 15 + 30 = 375` s | `evaluate/autowfbench.py` |
| Live grant | Expiry set before Harbor dispatch: build + agent + 120 trial overhead + 300 job overhead + execution window (+240 hosted RPC). 2220 s fixtures / 1980 s hosted | `coordinate/live.py`, `packages.py:runtime_grant_seconds` |
| Harbor phases | All tasks: build 600 s, agent 600 s; verifier per manifest (1800–6600 s), from native Harbor phase entry. Serial concurrency 1, automatic retries 0 | staged `task.toml`, recorded Harbor argv |
| Fixture verifier admission | Normal `max((positive + negative + 4)*execution_ceiling, live_ceiling) + 120`; lifecycle `(2*positive + 1)*bound_deadline + 120`; candidate deadline cannot exceed packaged budget | `coordinate/packages.py`, `verification/verify.py` |
| Hosted verifier admission | Execution `2*240 + 120 + 375 + 120 = 1095` s; generation admission 120 s | `coordinate/packages.py:hosted_verifier_seconds` |
| Legacy outer bounds | Job sum of build + agent + verifier estimate + 120 per trial, then +300 = 52620 s. Computed suite ceiling = local tests 300 + image build 1200 + transport 1800 + two jobs = 108540 s. Record the formula separately from whether a caller applies it | `coordinate/packages.py:job_seconds`, `controls.py:suite_seconds` |

These observations freeze current behavior; they authorize no deadline fixes in
ticket 01. Later timeout replacement must preserve start points and semantic
admission, and prove which outer limits can be removed.

## Historical records and supported read surfaces

This inventory separates available examples from a claim that all historical
records can already be re-evaluated by current code.

| Actual record/form | Existing reader / limitation |
| --- | --- |
| `sapi-lab-harbor/v1` controls; fixture `sapi-lab-verification/v1`, observation-plan/v1 and observation/v1 | `coordinate/runs.py:load_trials`, `coordinate/evaluation.py:trial_result`; `evaluate --record <verifier> --output <new> [--cases <staged-cases>]` needs the complete plan/evidence and matching evaluator/runtime identity. Historical fixture archive contains 137 files |
| `sapi-lab-simulator-trial/v1`, before native/terminal fields | Actual `refactor-controls` checkout oracle archive (15 files); absent newer fields remain null in hosted report/review views. Preserve legacy execution meaning; no fabricated native success |
| Same hosted schema with native/terminal fields | New all-eleven records and existing `cleanup-final-all-11`; `hosted_evaluation`, `load_trials`, review-export |
| `sapi-lab-upstream-acceptance/v1`, with/without `evaluation_path` association | `review_export.find_evaluations` prefers authoritative host report; older form resolves `environments/<job>/<task>/evaluation/report.json`. Synthetic missing-association coverage is `tests/test_review_export.py`; do not rewrite recorded rewards |
| `sapi-lab-task-evaluation/v1`, task-contract/v1, run log 1.0 and saved `judge-reply.json` | `evaluate --record <hosted> --output <new> --judgement <matching-reply>`; contract, source, judge and run must match. Old source mismatch must fail closed. No `--dispatch-judge` is needed for saved replies |
| `sapi-lab-rubric-evaluation/v1` | `trial_result` reads fixture rubric beside acceptance, preserves unscored values; it is not the hosted task-evaluation schema |
| Generation/v1, live/v1/v3, expansion/refinement live/selection/series, lifecycle/v1/experiment/v1, wrapper identity, rebuild dispatch | Concrete committed paths and schemas are enumerated in `historical-files.json`. These are experiment/consumer records, not interchangeable `evaluate --record` inputs; some committed copies are extracts without the referenced native tree |

Ticket 21 must prove historical reader compatibility without old execution
infrastructure. The inventory and actual archives prevent deleting a format based
on prose alone; this ticket does not implement a compatibility reader or claim
every old absolute path still exists. Original committed evidence was not edited.

## CLI and ownership limits

`cli.json` captures the exact supported option forms, not a reconstructed list.
Public commands remain `check`, `compile`, `build`, `harbor`, `generate`, `select`,
`live`, `evaluate`, `ui`, `lifecycle`, `review-export`, `fetch-source`. Internal
commands remain `execute`, `package-tasks`, `transport`, `bridge`, `hosted-worker`.
All nine fixtures are default; both hosted benchmarks require explicit selection.
Detached `compile` currently defaults to the global catalog; explicit `--bindings`
is supported. Preserve these forms while later moving ownership.

New ownership tests ratchet the existing dependency graph; they do not assert the
legacy implementation already conforms to `BENCHMARKS -> CORE -> HARBOR`.
See [the architecture migration constraints](../../docs/ARCHITECTURE.md#migration-ownership-constraints).
The independent verifier still imports only its own implementation. No loader,
positive packaging, new benchmark seam or Harbor lifecycle replacement has been
implemented here.

## Ticket 01 completion

- Eleven explicit oracle/nop pairs: passed; both Harbor job exits are 0.
- Exact prompt/catalog, semantic input and source/image hashes: captured; paid calls 0.
- Deadline, CLI, historical formats and locatable artifacts: captured, with actual historical archives.
- Migration import constraints: three new tests pass; 25 legacy edges explicitly frozen.

Original check: 424 tests passed. Final check: 427 tests, Ruff lint/format, mypy
(65 sources) and source/wheel distribution passed. Required targeted suites: 46
tests passed. The complete logs and exact commands are in `verification.json`.
Transport controls and final source-identity reconciliation passed. Final Docker
container listing is empty. Task 02 has not started.
