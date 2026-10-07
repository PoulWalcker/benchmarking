# Summary

One focused cleanup phase, verified against the complete architectural audit and its baseline commit `9690ba5`. Seven dependency-ordered implementation tickets were completed as separate commits. The existing author/compile/execute/evaluate pipeline, provider tables and independent verifier remain the architecture. No backend, provider-asset, lifecycle, plugin or business-rule framework was introduced.

Final validation: **all eleven oracle and nop controls passed**, along with real n8n transport/rejection probes. The full code check passes: 423 tests, Ruff, formatting, mypy and distribution/privacy checks. No paid model calls were run.

The interpretation remains INPUT (benchmark/task/environment/evaluator/bindings/budgets) → PROCESSING (validate, author, compile, execute, record, dispatch evaluation) → OUTPUT (evidence, execution, acceptance, optional quality, identity). Benchmark truth is trusted input to independent evaluation, not a new collection of coordinator branches.

# Tickets completed

| Ticket | Commit | Result |
| --- | --- | --- |
| [01 — Hosted preflight](tickets/01-hosted-preflight.md) | `610fae0` | Explicit local operation capability; honest hosted compilation/admission without invented model stubs |
| [02 — Fixture bindings](tickets/02-fixture-bindings.md) | `76f9f2c` | Selected catalog propagated through prompts, staging, execution, lifecycle and evidence reconstruction |
| [03 — Fixture ownership](tickets/03-fixture-ownership.md) | `885d3ba` | Independent evaluator composition owns domain checks, guarded-call expectations, rubric views, corruptions and freshness |
| [04 — Hosted contracts](tickets/04-hosted-contracts.md) | `0da9d4a` | Environment metadata independent of scorer loading; explicit options/result validation; separate native/terminal observations |
| [05 — Hosted lifetimes/evidence](tickets/05-hosted-lifetimes-evidence.md) | `659cd39` | Composed stage limits and durable, single-write terminal evidence on timeout/abort |
| [06 — Hosted review](tickets/06-hosted-review.md) | `02f2974` | Authoritative report association, hosted discovery and common-result rendering |
| [07 — Transport/controls](tickets/07-transport-controls.md) | `8b5469c` | Shared redirect rejection and narrowly justified nop exception policy |

The verification/classification and sequence were committed first as `94a1f78`. Each ticket records exact paths, acceptance criteria, tests, non-goals and implementation evidence. No finding was reopened after a post-audit fix: the starting code matched the audited commit.

# Findings fixed

| Finding | Resolution |
| --- | --- |
| F01 | Ticket 03: generic verifier planning, budget prediction and rubric aggregation use independent fixture evaluator callbacks; benchmark names live in their trusted composition owner. Business checks do not import runtime implementation. |
| F02 | Ticket 01: missing local implementations are rejected at compilation. Hosted unpaid preflight compiles for real live capabilities and enforces declared limits; it does not claim to execute or predict acceptance. Fixture replay and fresh hosted oracle/nop execution remain required. Regression reproduces the original missing-operation class of failure and covers `crm.plan`, `incident.plan`, `incident.summarize`. |
| F03 | Ticket 02: a selected catalog reaches authoring, CLI/container observation, lifecycle validation/repair/execution and live evidence/prompt-hash reconstruction. An empty supplied catalog cannot silently select the global one. |
| F06 | Ticket 04: AutoWFBench adapter reads verified task definition bytes for instructions/duration without loading its evaluator contract. |
| F08 | Ticket 04: additive `native_execution` and `terminal_completion` in hosted trial/report records distinguish the facts. Historical `result.execution` and schema identifiers retain their meaning. A state evaluator can accept native success without narrative completion. |
| F09 | Ticket 05: evaluator owns its aggregate duration; worker finish RPC, verifier and Harbor contain it. Runtime grants account for startup before the environment window and start after evaluator preparation. Model-call/backend/provider transport limits remain locally owned. |
| F10 | Ticket 05: deadline and orderly abort persist available environment, transport, native and trial observations once. Missing native outcome is unknown, no successful output or acceptance is fabricated, and interrupted trials do not dispatch evaluation. Partial collection errors are explicit. |
| F11 | Ticket 06: trial rows associate authoritative evaluation paths; export renders hosted common facts, null quality and links to provider details. Existing host layout is supported for old reports. Evidence and hosted rewards stay untouched. |
| F13 | Ticket 03: freshness is explicitly selected by the four existing composed fixture evaluators; generation only records/stages the resulting overlay. Unknown freshness contracts fail clearly. |
| F14 | Ticket 04: frozen `ReevaluationOptions` replaces a CLI namespace; malformed normalized results fail at dispatch, with finite bounded scores and null unscored values. No evaluator hierarchy. |
| F16 | Ticket 07: author and hosted worker use the existing no-redirect client. Real HTTP regression proves redirect targets receive no forwarded request. |
| F17 | Ticket 04: executor and CRM activation terminology match their responsibility; the universal synthetic-world wording was removed from hosted authoring. Two hosted prompt hashes were deliberately revised; fixture prompt hashes and durable protocols were retained. |
| F18 | Ticket 07: rejected unscored nop allows only the exact expected `RewardFileNotFoundError` with absent reward; deterministic null-quality nop requires reward zero and no exception. All jobs must exit successfully. |

F18 audit refinement: actual pinned Harbor runs return exit code zero for the expected missing-reward trial exception. The broad nonzero-job bypass was unnecessary and was removed. No architecture contradiction or additional approval was needed.

# Findings deferred

[DEFERRED_ARCHITECTURE.md](DEFERRED_ARCHITECTURE.md) records why each pressure is real, why it is not a current fix, and its trigger.

| Finding | Deliberate limit |
| --- | --- |
| F04 | Keep n8n specialization until a second real backend supplies capabilities, native evidence and controls. |
| F05 | Keep existing provider configuration/provenance convention and seed zero until a second provider or real multi-seed experiment needs more. |
| F07 | Keep the existing digest lifecycle and injected verifier/rebuilder seam until a second lifecycle use case exists. Catalog propagation was fixed, not generalized lifecycle policy. |
| F12 | Keep explicit source/privacy/packaging enrollment until a real provider needs new asset types; that extension must test identity and visibility together. |
| F15 | Keep precise current provider isolation checks until a second real provider can validate broader conformance requirements. |

The reusable receipt/transport extraction aspect of F09 is also deferred until a second provider needs the same implementation. Independent verifier arithmetic and schemas, pinned tool versions/revisions/images, explicit safety/resource limits, stable endpoints/receipts, historical schema names and narrowly supported historical records are intentionally kept.

# Architecture before

Fixture benchmark knowledge was spread through business dispatch, generic planning, guarded-call accounting, rubric extraction and authoring freshness. Scenario catalogs could be lost between authoring and container execution. Hosted catalogs advertised model operations that mandatory stub replay could not execute. Environment metadata unnecessarily loaded evaluator machinery.

Hosted evidence depended on `/finish`; worker loss could discard state. Stage durations did not compose consistently, legacy execution included completion validity without a separate native fact, and review export missed authoritative hosted reports. Two clients bypassed redirect policy and nop tolerated unrelated infrastructure failures.

# Architecture after

The same stage graph remains. A small independent fixture composition table now selects existing business checks/procedures, rubric views, corruptions and optional freshness. It is an evaluator implementation owner, not another scenario registry or a declarative business language. Scenario discovery remains `benchmarks/*/scenario.json`.

Bindings are an explicit pipeline input. Compiler capability admission is separate from proving execution or task correctness. Hosted environment and evaluator adapters own their metadata and timing; coordination composes their declared bounds, validates normalized results and preserves evidence/report associations. One locked terminal recorder covers finish, timeout and abort before any evaluation.

The additions are small concrete contracts: fixture composition, evaluator options, declared evaluator duration and two observed execution facts. These replace scattered dispatch rules and implicit assumptions. They do not add plugin discovery, inheritance, universal configuration or a second orchestration system.

# Tests

- Ticket 01: 74 targeted tests; full check 399; real CRM controls and unpaid hosted admission.
- Tickets 02/03: 79 and 103 targeted tests; full check 404; real lifecycle/composed fixture controls.
- Ticket 04: full check 409; metadata independence, malformed evaluator output, deterministic null quality and native/terminal separation.
- Ticket 05: 69 targeted tests; full check 417; real checkout controls; actual timer expiry, finish/timeout race, abort, partial collection/evaluator failures, composed budgets.
- Ticket 06: 64 targeted tests; real historical checkout/CRM discovery dry runs; authoritative report preference and evidence/reward immutability.
- Ticket 07: 32 targeted tests; final `uv run --locked sapi-lab check` passes with **423 tests**, Ruff, format, mypy and distribution checks. Ten private sentinels remain excluded.
- Final unpaid all-eleven oracle/nop, real n8n transport/rejection probes and source/image checks: **passed**, both Harbor jobs exit zero. The gate ran from 11:41:32 to 11:54:19 UTC on 2026-10-07 (about 13 minutes). Existing container identities were preserved.

Final control command explicitly selects invoice-total, ticket-routing, competitor-report, revise-answer, daily-digest, dual-ledger-closeout, support-review-packet, bulletin-market-brief, priority-support-brief, checkout-recovery and crm-lead-qualification. Local run artifacts are under `reports/cleanup-final-all-11/`; byte-for-byte committed extracts are linked below. No recorded artifacts were rewritten.

Committed validation artifacts:

- [All-eleven control report](../evidence/cleanup-final-all-11/report.json), [source manifest](../evidence/cleanup-final-all-11/source-manifest.json), [transport summary](../evidence/cleanup-final-all-11/transport/summary.json), [runtime versions](../evidence/cleanup-final-all-11/runtime-versions.txt).
- [Full check log](../evidence/cleanup-07-check-final.log), [review-export dry run on final controls](../evidence/cleanup-final-all-11/review-export.json).
- [Ticket 01 unpaid hosted preflight](../evidence/cleanup-01-preflight-verified/report.json) and [its source manifest](../evidence/cleanup-01-preflight-verified/source-manifest.json). This is a ticket-stage proof, not a claim that its manifest equals the final tree.

Final implementation commit: `8b5469c`. Harbor `0.21.0`; n8n `2.41.5`. Final source-manifest SHA256: `c8104e636ca13860358b344ec61badb4716a1d8999d2da3bd9e0de8c39b8a3dc`. Image identity: `sha256:5af8057e8e2fda68202dc270da9ad2f73547e145122796165c24fa00892b4615`. Raw native/container artifacts remain in the local run directory; internal extract links can name files not committed here.

# Behavior preservation

The independent fixture checks, positive/negative cases, corruptions, refinement/lifecycle behavior, and freshness relationships are retained. Verification continues to import only its own modules; boundary tests also prohibit benchmark-name literals in generic planning/scoring/generation.

Hosted acceptance and upstream scoring rules remain unchanged. `quality=null` remains valid for evaluators without quality, while an unscored quality result retains null numeric fields. The added native/terminal observations do not reinterpret historical records. Prompt wording and CRM activation names are deliberate source/experiment revisions, not claims that paid model responses are identical; no paid equivalence experiment was performed.

| Scenarios | Oracle acceptance / reward | Nop acceptance / reward | Nop exception |
| --- | --- | --- | --- |
| 01 invoice-total, 02 ticket-routing, 03 competitor-report | true / 1.0 each | false / 0.0 each | none |
| 04 revise-answer, 05 daily-digest, 06 dual-ledger-closeout | true / 1.0 each | false / 0.0 each | none |
| 07 support-review-packet, 08 bulletin-market-brief, 09 priority-support-brief | true / 1.0 each | false / 0.0 each | none |
| 10 checkout-recovery | true / 0.732 | false / null | expected missing reward |
| 11 crm-lead-qualification | true / 0.732 | false / null | expected missing reward |

Both hosted nop results retain null scores, with exactly `RewardFileNotFoundError`. Both control jobs exit zero; every oracle trial has no exception. Deterministic hosted null-quality oracle/nop behavior is additionally covered by the fake-provider integration tests.

Evidence is still immutable and evaluation derived. Timeout/abort records state without successful submission, acceptance or a paid judge call. Reference rewards, source revisions, version/image identity requirements, protocol names and recorded evidence schemas remain intentional pins.

# Remaining known limitations

- Only n8n is implemented. Native graph inspection, corruption probes, timing, runtime identity, packaging and lifecycle evidence retain n8n details.
- The compiler still bundles the present local operation implementation library. A new local operation needs real code plus independent verification; a catalog declaration alone cannot create behavior. Hosted admission proves compilation/limits, not candidate usefulness or live model success.
- Fixture `roles.OUTPUTS` is an independent operation-output contract; new output vocabulary may require updating that trusted owner. New execution procedures can require genuine generic protocol work. The guarantee is for normal additions using existing mechanics, not every conceivable benchmark.
- Lifecycle policy remains the single existing digest use case. Provider configuration, seed selection and private asset enrollment remain deliberately explicit.
- Abort persistence covers a running host's deadline/cleanup, not host process/OS death, disk failure recovery or cancellation of remote operations. Interrupted trials retain evidence but do not automatically obtain a reevaluation package or verdict.
- Existing synchronous provider startup/transport ceilings remain bounded. A provider needing longer startup or another transport must demonstrate and declare its real requirements; no universal timeout framework was built.
- Hosted execution supports one attempt per fresh environment. Additional attempts require fresh environment/evidence ownership rather than reuse.
- Review discovery supports current and historical recorded layouts, not arbitrary relocation of directories containing absolute associations. Specialized rubric details remain separate from common result rendering.
- Candidate isolation remains the existing upload-only model. Browser/shell agents or UI environments require their own concrete trust-boundary work.

# Open/Closed pressure-test result

| Addition | Changes now required | Comparison with audit |
| --- | --- | --- |
| Normal fixture scenario | Benchmark data, independent business evaluator and one entry in `verification/fixture_evaluators.py`, plus tests/prompt pins. **Zero generic production machinery files** for existing execution mechanics/vocabulary. Optional rubric, guard or freshness callbacks stay with the trusted evaluator. | Previously richer scenarios spread domain decisions into `verify.py`, `rubric_facts.py` and `generate.py`; catalogs could diverge. Those generic edits are no longer required. |
| Existing-provider hosted scenario | Benchmark files, tests/prompt pins, and a deliberate source-pin update if its upstream challenge is not already pinned. Adapter changes only for genuinely unsupported upstream semantics. **No generic core edit** for a compatible scenario. | Removes the mandatory missing-stub blocker for live model operations. Existing adapter ownership is preserved. |
| Deterministic hosted evaluator | Evaluator code/tests, registration and environment compatibility in `providers.py`, positive aggregate duration, benchmark evaluator selection and isolation coverage. Return common result with null quality; explicit reevaluation options replace argparse. **No generic orchestration edit.** | Existing null-quality support is preserved; execution facts, option/result boundary and review discovery now work without provider-specific core branches. |
| FastAPI/Postgres hosted provider | Environment/session and evaluator implementations, provider table entries/module privacy inventory, scenario data and real controls. Dependencies/HostConfig only if actually needed. SQL/CSV assets require explicit source/privacy/packaging enrollment and tests. | Registration and concrete provider implementation remain the expected work. Composed evaluator/environment limits and state-only acceptance remove current obstacles; asset/config policies are explicitly deferred, not hidden. |
| Second backend | Backend compiler/executor, native verifier/controls and image; actual selection in `coordinate/backend.py`; adapt observe mutation, package timing, transport/image controls, live graph evidence, host solution identity and lifecycle native outcomes. | F04 remains intentionally real. Provider worlds, benchmark business checks and high-level supported semantics should stay independent. No backend framework was implemented. |

# Recommendation

1. **Ready to stop refactoring?** Yes. The final all-eleven gate passed. The concrete cleanup is complete; further broadening now would be speculative.
2. **Stable mental model to study?** Yes: definitions compile to artifacts; execution records evidence; independent evaluation derives execution/acceptance/optional quality. Read benchmark truth at its trusted owner and provider specifics in its adapter.
3. **Safe to start the n8n MCP experiment next?** Yes as a bounded materialization experiment; the full gate passed. Keep existing admission, immutable evidence, native verification, budgets and controls. Any new browser/shell access or external side effects require the actual experiment's isolation design; this cleanup does not establish those new guarantees.
4. **What remains untouched until a second real use case?** Backend registries/IR, universal provider configuration/assets, generic lifecycle/scheduling, shared provider transport/receipts and plugin/conformance discovery. Preserve pins, independent verification, stable protocols and result compatibility throughout.
