# Post-migration architecture audit

## 1. Executive summary

Audited `migration/benchmark-ownership` at `872aea76a493a82562dea391371c38de4397d3df` on 2026-10-09, including actual implementation, callers, tests, packaging and installed Harbor 0.21.0 source. The pre-existing blank-line change in the invoice manifest was left untouched. This audit changes no production code.

The migration established useful boundaries: explicit benchmark descriptors, benchmark-owned operations/worlds/evaluators, positive public/trusted/reference packaging, independent recorded-evidence evaluation, and external Harbor infrastructure. These should remain. No critical issue or justification for another large migration was found.

Two confirmed integration gaps deserve correction before expanding paid experiments: fixture live preflight now skips independent replay, and trial readers ignore the normalized result written by the new worker. Additional complexity comes from inconsistent hook options, a transport test harness inside coordination, mixed test ownership, and obsolete packaging material. Most cleanup can wait.

Validation: `uv run --locked sapi-lab check` passed: **498 tests run, 7 skipped**, Ruff lint/format, mypy and direct-wheel/sdist installation checks. Two temporary, unpaid Python probes reproduced F1 and F2 below. No fresh Docker control or model call was made; passing local checks is not fresh native execution evidence.

## 2. Findings

Finding locations below are repository-relative. Later discussion abbreviates stage paths under `src/sapi_config_lab/`. “Confirmed” describes an observed implementation fact; a recommended placement is explicitly labeled a preference where behavior is presently correct.

### F1 — High · Confirmed: fixture preflight silently becomes compilation-only admission

- **Files/symbols:** `src/sapi_config_lab/coordinate/live.py:main, check_trials`; `src/sapi_config_lab/coordinate/runs.py:Run.harbor`; `src/sapi_config_lab/coordinate/benchmark_worker.py:main, admit`.
- **Current responsibility/problem:** `live.main` sends the entire stub-replay cohort with `admission=True`. `Run.harbor` sets `SAPI_HOSTED_ADMISSION=1` for all tasks. The worker then skips planning, observation and evaluation even when no `prepare` hook exists. `check_trials` accepts the resulting admission report for a fixture benchmark. This contradicts the documented fixture replay gate and can defer discovery of a business-invalid submission until live execution.
- **Evidence:** changing invoice `workflow.output` to a literal incorrect total/count of `999` still produced `admit(...)["passed"] == True`; `check_trials(..., admission=True)` accepted it. The existing hosted-preflight tests exercise checkout but do not enforce fixture replay through this composition.
- **Recommendation/simplification:** restrict admission-only behavior to descriptors with the world preparation hook; fixture tasks must follow their existing plan → observe → evaluate route. Enforce that distinction when accepting preflight records. One explicit selection rule replaces an ambiguous global switch.
- **Regression risk:** medium; preserve checkout's intentional compilation-only preflight and prevent world startup there. This restores an existing gate rather than changing business acceptance.

### F2 — High · Confirmed: the normalized verdict is written but bypassed by readers

- **Files/symbols:** `src/sapi_config_lab/coordinate/benchmark_worker.py:main`; `src/sapi_config_lab/evaluate/records.py:validate_result, load_trials, trial_result, trial_accepted`; `src/sapi_config_lab/coordinate/live.py:check_trials`; `tests/support/extensibility/evaluation/evaluator.py:evaluate`.
- **Current responsibility/problem:** the worker writes `verifier/result.json`, but `load_trials` reconstructs results from `evaluation/report.json` using fixture/hosted schemas. `check_trials` also branches on those schemas. A new evaluator returning the documented normalized result still needs undocumented report compatibility. The beacon evaluator writes a normalized report without either recognized schema, exposing this gap. Additionally, the worker only checks two optional boolean values, whereas re-evaluation uses the stronger `validate_result` contract.
- **Evidence:** a temporary Harbor-shaped trial with reward `1`, no exception, and `{execution: true, acceptance: true, quality: null}` in both verifier result/report files loaded as three null facts; `control_passed("oracle", ...)` returned false. Extension tests cover direct evaluator/native results but do not prove the complete `./run.sh` control-reader path for this shape.
- **Recommendation/simplification:** make the recorded normalized verdict authoritative for versioned trials; retain existing report readers as historical fallbacks only. Validate the same result contract at worker and re-evaluation boundaries, with a small neutral contract function if needed to preserve stage imports. Keep submission/mode/case identity checks using recorded metadata and observation evidence, not business report schemas. Keep admission distinct from evaluated acceptance and preserve benchmark reward projection.
- **Regression risk:** medium; malformed new results must fail closed, not fall back to a favorable old report. Preserve historical missing facts, original rewards and checkout's `0.732` reference score.

### F3 — Medium · Confirmed: hook inputs have inconsistent configuration and policy sources

- **Files/symbols:** `src/sapi_config_lab/coordinate/packages.py:selected_cases, verifier_bounds, stage_tasks`; `src/sapi_config_lab/coordinate/benchmark_worker.py:main`; `src/sapi_config_lab/coordinate/live.py:cohort_grants`; `src/sapi_config_lab/coordinate/live_evidence.py:collect_native`; `src/sapi_config_lab/coordinate/evaluation.py:reevaluate_benchmark`; `verification/verify.py:plan`.
- **Current responsibility/problem:** `config` is declared opaque, yet coordination interprets `cases` and requires `deadline_seconds` for world-backed admission. The worker supplies `options["config"]`; host planning and re-evaluation do not consistently supply it. Fixture live deadline policy is independently encoded as `600` in several callers and the verifier. Existing benchmarks mostly read local files/hardcoded task constants, masking this inconsistency.
- **Recommendation/simplification:** document the few existing experiment-option fields and consistently pass selected descriptor configuration to every hook invocation, including offline evaluation. Supply the admitted deadline explicitly and verify it independently rather than deriving it differently at each call site. Do not add a provider registry or interpret additional business keys in core.
- **Expected simplification:** one explicit input contract for staging, execution and re-evaluation; fewer hidden requirements for the next benchmark.
- **Regression risk:** medium; preserve current deadline values, option hashes, fixture selections and prompt bytes. Changed source/options identities require fresh controls, not rewritten evidence.

### F4 — Medium · Confirmed: transport diagnostics retain a broad legacy packaging path

- **Files/symbols:** `src/sapi_config_lab/coordinate/transport.py:FakeBridge, BridgeHandler, run_probes`; `src/sapi_config_lab/coordinate/controls.py:transport_probe`; `src/sapi_config_lab/coordinate/runs.py:Run.transport, use_image, stage`; `infra/Dockerfile`; `src/sapi_config_lab/harbor_integration/runtime/Dockerfile`; `tests/support/native-transport/`.
- **Current responsibility/problem:** a 306-line adversarial test server/assertion suite lives in coordination. Its control image copies core, generation and benchmark trees using exclusions, although its operations/configuration are already test-local. Runtime Docker pins are duplicated in two recipes. `Run.transport` dispatches separately from `runner.run_job`. The lab image is pinned and gates live runs, but ordinary positive task Dockerfiles do not use that local image: their runtime is built from the separate pinned upstream recipe. Thus the lab image identity attests the transport control, not the actual task image.
- **Recommendation/simplification:** keep the unpaid native probe, move its implementation beside its test fixtures, and preserve the existing internal CLI as a thin entrypoint. Give the control an explicit minimal input closure and reuse the clean runtime recipe without inheriting a private checkout image. Retain its deliberate shared/nop-only profile; do not force it through separate-verifier validation. Clarify control-image versus task-runtime provenance before removing any gate.
- **Expected simplification:** eliminate unrelated benchmark copies and duplicate runtime recipe maintenance; leave coordination responsible for dispatch and report references.
- **Regression risk:** medium; preserve timeout/redirect/malformed-response probes, `--skip-build`, diagnostics and Harbor cleanup. This is control maintenance, not evidence of current candidate leakage through positive packages.

### F5 — Medium · Confirmed coupling; placement recommendation: tests do not follow ownership

- **Files/symbols:** `tests/test_workflows.py:LabTests`; `tests/test_simulator_environment.py:SourceCacheTests, CheckoutRetryPolicyTests`; checkout files enumerated in section 3; `tests/test_task_evaluation.py`; `tests/test_judge_calibration.py`; `tests/support/invoice.py:fixture`; `src/sapi_config_lab/coordinate/cli.py:CHECKS`; `src/sapi_config_lab/coordinate/controls.py:main`.
- **Current responsibility/problem:** root tests mix compiler/verifier mechanisms with invoice arithmetic and checkout world/scoring behavior. Core tests load invoice-private implementation via `hooks.plan.__globals__["_fixture"]`. Both check commands discover only root `tests/`, so moving files alone silently loses coverage. The current positive manifest packaging excludes development tests, but the transport tree-copy path would copy newly benchmark-local tests unless adjusted.
- **Recommendation/simplification:** move business tests and their helpers to benchmark-local development `tests/`; split mixed modules by assertions, following section 3. Keep generic mechanism tests in root and gradually use small explicit test fixtures there. Update both discovery entrypoints and type/lint handling without creating a second benchmark registry. Exclude development tests from runtime manifests and control image inputs.
- **Expected simplification:** each benchmark can change its business rules without forcing unrelated mechanism-test edits; fewer private implementation dependencies.
- **Regression risk:** medium; preserve discovered test identities/counts, optional native tests and installed-resource isolation. Benchmark development tests are not Harbor runtime `tests/` payloads.

### F6 — Low · Confirmed: obsolete material remains on the active maintenance surface

- **Files/symbols:** `benchmarks/01-invoice-total/scenario.json:config.legacy`, `benchmarks/10-checkout-recovery/scenario.json:config.legacy`; `harbor/templates/{test.sh,task.toml,solve.sh}`; `src/sapi_config_lab/coordinate/wrapper.py`; `provenance/autowfbench-source.json`; `src/sapi_config_lab/coordinate/provenance.py:SOURCE_DIRECTORIES`; `tests/test_packaging.py`; `tests/test_rubric_wiring.py:VerifierSeamTests`.
- **Current responsibility/problem:** no current consumer reads `config.legacy`; it is merely serialized/hashed. It duplicates budgets, reward, environment labels and old timeout/output settings. The old templates have no active task-building caller; their shell verifier invokes a removed `verify.py` CLI. Tests still inspect these templates, creating misleading coverage. `coordinate.wrapper` only re-exports two integration functions to current callers. The root AutoWFBench pin is historical; active resolution selects checkout's declared pin.
- **Recommendation/simplification:** remove unused legacy fields and obsolete templates; replace template-text assertions with checks of the actual staged worker/reward path. Import wrapper identity functions from their owning integration module. Remove the duplicate root pin only after recording its historical retrieval location. Keep sealed evidence and snapshot readers intact (section 4).
- **Regression risk:** low for active runtime, medium for provenance. Manifest/source hash changes invalidate old run matching intentionally; retain the audited Git revision and matching evaluator snapshots for reproduction. Do not update historical hashes to make them match new sources.

### F7 — Low · Confirmed: catalog reduction has two implementations

- **Files/symbols:** `src/sapi_config_lab/coordinate/packages.py:scenario_catalog`; `src/sapi_config_lab/coordinate/benchmark_authoring.py:generation_prompt`; `src/sapi_config_lab/coordinate/generate.py:main`.
- **Current responsibility/problem:** both functions independently filter binding YAML by reference operations. Only the prompt path verifies semantic equivalence after filtering. Generation records catalog hashes using the other implementation, so prompt construction and reported provenance can drift.
- **Recommendation/simplification:** one byte-preserving catalog reducer in `benchmark_authoring.py`, used for both prompt composition and report metadata. This has two demonstrated callers; no generic catalog framework is needed.
- **Regression risk:** low if existing full/scenario prompt and catalog hashes remain exact; YAML reserialization is not an equivalent replacement.

### F8 — Medium · Architectural preference: lifecycle contains runtime policy inside coordination

- **Files/symbols:** `src/sapi_config_lab/coordinate/lifecycle.py:LifecycleController, validate_lifecycle, main`; `src/sapi_config_lab/coordinate/observe.py:_lifecycle`; `verification/lifecycle.py:verify_lifecycle`.
- **Current responsibility/problem:** the 762-line controller owns SQLite transactions, event deduplication, scheduling, revision release/archive, rebuild reservations and crash recovery as well as CLI composition. These are execution semantics, not merely sequencing stages. However, this is exercised functionality, not dead code: lifecycle/refinement fixtures and native controls test it. Harbor retries do not implement these workflow semantics.
- **Recommendation/simplification:** defer until lifecycle work resumes. At that point move the existing runtime controller behind the execute boundary, pass its execution callable explicitly, and retain CLI composition/compatibility at the current entrypoint. Update boundary tests and architecture documentation together. Keep a single concrete controller; do not split it into a repository/service hierarchy or introduce an abstract scheduler.
- **Expected simplification:** clearer dependency direction and a coordinator that composes runtime policy; little immediate code deletion.
- **Regression risk:** high relative to the benefit because of durable-state/recovery contracts. This is not an experiment-readiness prerequisite.

## 3. Ownership review

| Area | Current responsibility | Recommended boundary |
| --- | --- | --- |
| Invoice-total | Local bindings, operation bundle, public task/prompt inputs, private cases, independent arithmetic, role/output contracts, binary rubric and native task settings | Keep. Move invoice business development tests here; remove unused legacy metadata only. |
| Checkout-recovery | Local operations, Compose world, installer/source pin, receipts/deadline/snapshot hooks, completion contract, upstream scoring and calibration | Keep. Move checkout behavior tests here. AutoWFBench is an active private runtime/evaluation dependency, not merely a provenance label. |
| Core | Profile/compiler, execution/evidence, descriptors/loading, experiment budgets and coordination | Keep generic stages. Repair normalized result/options composition; separate transport test behavior and later lifecycle runtime policy. |
| Independent verification | Native provenance, role binding, evidence integrity, refinement/lifecycle checks and optional rubric mechanics | Keep independent of compiler/runtime. Benchmarks supply expected values and scoring contracts. |
| Development tests | All in root, including business assertions and cross-boundary controls | Benchmark business tests local; shared mechanisms and integration tests root. Never add these files to candidate payload declarations. |
| Harbor | Container/service startup/teardown, phase limits, artifact transfer and trial output | Keep external 0.21.0. Adapter owns positive packaging and protected YAML collection; benchmarks own world semantics. |

**Invoice independently:** its bundle includes ticket, research, reply and digest operations unrelated to invoice arithmetic. They remain advertised in the hash-pinned full catalog and used by test-only graphs. Removing them would change the full-catalog experiment and supported submissions. Keep that compatibility surface; a reduced catalog is already an explicit generation arm. Do not move these operations back into core or resurrect retired benchmarks.

**Checkout independently:** `environment/server.py:World` enforces semantic deadlines, receipt replay and bounded tool retries; hooks freeze actual observations; the evaluator reads those observations. These are benchmark behaviors, not duplicate container orchestration. The source/wheel installer and isolated scoring subprocesses keep pinned third-party code private and reproducible. No new benchmark-specific core operation dispatch was found.

**Adding a benchmark:** directory discovery, explicit operations, import closure validation and wheel inclusion require no core registry edit. `tests/support/extensibility/` demonstrates an unrelated world. End-to-end readiness is narrower than that proof: F2 blocks normalized-only results in the control reader; F3 affects hooks relying on configuration options.

### Development-test classification

| Destination | Concrete tests/helpers | Reason |
| --- | --- | --- |
| Root core tests | `test_profile_validation`, `test_backend_contract`, `test_operation_bundle`, `test_operation_transport`, `test_agency`, `test_occurrence_budget`, `test_runs`, `test_live`, `test_generation`, `test_selection`, `test_ui`, `test_rebuilder`, `test_lifecycle`, `test_refinement`, CLI/manifest/loading/retirement/distribution tests | Contracts of shared mechanisms and experiment commands. Synthetic ticket/research graphs remain test specimens, not restored benchmarks. |
| Root shared verification tests | `test_evidence_boundary`, `test_n8n_evidence`, `test_role_lineage`, `test_occurrences`, `test_refinement_verification`, `test_lifecycle_verification`, `test_rubric`, `test_rubric_wiring`, mechanism portions of `test_verification_contract` | Independent provenance, corruption rejection, graph equivalence, quality/null semantics; using invoice data alone does not make a test invoice-owned. |
| Root Harbor/integration tests | `test_benchmark_packages`, `test_harbor_settings`, `test_harbor_runner`, `test_transport_controls`, `test_benchmark_worker`, `test_native_budgets`, `test_benchmark_extensibility`, `test_cli_composition`; cross-boundary portions of `test_invoice_package`, `test_harbor_checkout`, `test_hosted_preflight` | Packaging/collection isolation, external infrastructure, normalized verdicts and public command composition. Keep package isolation assertions independent of benchmark implementation. |
| `benchmarks/01-invoice-total/tests/` | Three invoice arithmetic/invalid-batch tests in `test_workflows`; invoice plan/business/corruption expectations from `test_invoice_package` and `test_verification_contract`; business-specific helpers currently in `tests/support/invoice.py` | Invoice amounts, currencies, fixtures and expected intermediate results belong with the evaluator. Split mixed files; leave compiler rules in root. |
| `benchmarks/10-checkout-recovery/tests/` | `test_checkout_reporting`, `test_checkout_failure_evidence`, checkout world portions of `test_checkout_package_environment`/`test_checkout_isolation`, `test_checkout_package_evaluator`, `test_task_evaluation`, `test_judge_calibration`, `CheckoutRetryPolicyTests`; checkout helper/probe files | Completion fields, receipt policy, private world state, scorecard and judge calibration are checkout contracts. Keep generic source-cache tests and control-result rules in root. |

Use a single aggregate development runner that discovers root tests and each declared benchmark's local tests with distinct module identities. Account for `infra/check_types.py` checking benchmark directories and the root-only test lint exception. Assert a sentinel development file appears in neither author nor verifier runtime payloads/images. The source distribution may contain development tests; the candidate image must not.

Runtime evaluation remains in benchmark `evaluation/`, `verifier.sh` and generic `verification/`. Harbor's generated `tests/test.sh`, `tests/payload` and `tests/core` are a deployment layout, not the repository's development test suite.

## 4. Simplification candidates

| Class | Candidate | Decision |
| --- | --- | --- |
| REMOVE | Unread `config.legacy`, obsolete `harbor/templates/*`, internal wrapper re-export, duplicate current root source pin | F6, with historical retrieval preserved and assertions redirected to active code. |
| MOVE | Benchmark business tests; transport fake server/probes | F4–F5; update discovery and positive closures in the same task. |
| MOVE | Lifecycle runtime controller | F8, deferred preference; preserve state and public command contracts. |
| MERGE | Catalog subset construction | F7; retain exact prompt bytes. |
| SIMPLIFY | Versioned result reading and hook inputs | F2–F3; use explicit existing contracts, keep historical readers as fallbacks. |
| SIMPLIFY | Fixture versus world preflight routing | F1; restore fixture evaluation without adding an execution pipeline. |
| KEEP | `benchmark.py`, `benchmark_loading.py`, `benchmark_discovery.py` | Metadata validation, verified code loading and command selection have different inputs/outputs. Loader namespace/cache/path checks are necessary isolation, not a hypothetical plugin framework. |
| KEEP | `packages.py`, `benchmark_packages.py`, `harbor_integration/tasks.py` | Admission/experiment inputs → explicit generic runtime closure → native Harbor materialization are distinct responsibilities. Remove duplication inside them, not the boundaries. |
| KEEP | `runs.py`, ledger, live evidence reconciliation | Source guards, reservation-before-dispatch, unknown outcomes, report finalization and native/model identity matching are experiment policy Harbor does not supply. No extra outer trial watchdog was found. |
| KEEP | Independent verifier checks and optional rubric | `roles`, `n8n_provenance`, `refinement`, `lifecycle` check different obligations. Planning probes test the compiler/instrument; they are not duplicate business scoring. `rubric_facts` separates acceptance from quality. |

Verification has no active benchmark-name dispatch table. Its duplicate YAML/hash helpers versus core preserve independence; do not make it import the implementation it audits. Generic rubric arithmetic and checkout's pinned upstream scoring also have separate contracts; merging them could change historical scores. The fixture callback object is composition, not an inheritance framework. Optional judged-rubric behavior currently has richer test coverage than production usage (invoice uses a binary card, checkout uses upstream scoring); freeze that scope rather than expand it. Flat/package import branches remain covered by `StandaloneDistributionTests` and `test_packaging`; they are not safe dead-code deletions without explicitly retiring that distribution contract.

**Historical preservation:** retain `evidence/`, especially `migration-01-baseline/{INDEX.md,SHA256SUMS.json,source-manifest.json,historical/}`, `tests/support/historical-evaluator/{capture.json,autowfbench.py.txt}`, `coordinate/historical_evaluation.py` and historical readers. Preserve original evaluator bytes, dependency pins, prompts and schemas under their original revision/snapshot identities. A cleanup can delete an active duplicate while keeping its original path/hash reconstructible from that revision. Do not substitute current checkout scoring for archived scoring, edit old rewards, or delete caches until pinned source recovery is verified.

**Harbor verification:** installed `harbor/trial/trial.py:_run_separate_verifier, _separate_verifier_env` performs transfer, verifier startup/timeout and teardown; `harbor/environments/docker/docker.py:start, stop` manages Compose services and cleanup. The repository adapter invokes native jobs with serial execution and zero retries. Submission admission, model accounting, business-world deadlines and workflow rebuilds are not replacements for those Harbor facilities.

**Portability/dependencies:** active source inspection found no user-specific absolute host path. `execute/host.py:HostConfig` exposes machine settings; `/tests`, `/submission`, `/logs` and the simulator hostname are container contracts. macOS/Linux/POSIX and Docker assumptions remain intentional (`fcntl`, `os.link`, UID 1000); Windows execution is not established. Keep Python/n8n/Harbor pins, PyYAML, and checkout's fastjsonschema dependency. The optional dependency groups avoid forcing Harbor/AutoWFBench onto detached compilation. No dependency removal is justified by this audit. Stale template/import comments can disappear with their code; a documentation rewrite or file-count reduction is not warranted.

## 5. Refactoring priorities

Each task should be a separate change with the normal full check. Only T1 is a paid-fixture gate prerequisite; T2 is required before claiming arbitrary new benchmark control support. Cleanup tasks need not delay current unpaid experiments.

| Task / scope | Expected result and affected files | Dependencies | Acceptance criteria and required regressions |
| --- | --- | --- | --- |
| **T1: repair preflight routing** | F1; `coordinate/{live,runs,benchmark_worker}.py` | None | Wrong-but-compilable invoice is rejected before runtime model dispatch; valid invoice is independently replayed; checkout admission never starts a world. Add mixed-cohort regression in `test_live`/`test_hosted_preflight`, retain worker tests; run unpaid native controls for both benchmarks. |
| **T2: use the normalized versioned result** | F2; `evaluate/records.py`, worker/evaluation/live consumers, neutral validation contract if required | None | New evaluator can return a normalized verdict without imitating fixture/hosted reports; malformed quality/reward facts fail closed; admission remains separate. Extend `test_evaluator_contracts`, worker/readers, extension tests and legacy-record tests. Run beacon through the actual control-reader path plus invoice/checkout oracle/nop. |
| **T3: align hook inputs** | F3; package planning, worker, grants, native collection and re-evaluation | None; coordinate any overlapping T2 edits | A temporary benchmark whose hooks consume `options["config"]` receives identical immutable configuration in all paths; existing plans/deadlines remain exact. Extend manifest/extensibility, native-budget, invoice and offline checkout evaluation tests; rerun native controls. |
| **T4: merge catalog reduction** | F7; `benchmark_authoring.py`, `packages.py`, `generate.py` | None | Both callers use identical bytes; all full/scenario prompt pins unchanged. Run fixture-binding, packaging, generation and selection tests; no new Docker behavior. |
| **T5: remove obsolete active material** | F6; two manifests, old templates, wrapper imports, root pin, template-dependent tests | None; record preservation references first | No runtime caller depends on deleted paths; historical read/re-evaluation still uses matching snapshots. Replace template checks with staged worker reward tests; run packaging, rubric-wiring, legacy-record and distribution suites, then fresh oracle/nop because source/package identities change. |
| **T6: narrow the transport control** | F4; transport CLI/probe, `controls.py`, runtime recipe/control Docker inputs, native-transport fixtures | None | Same native probe outcomes and report paths; no benchmark tree needed in control image; runtime pins have one owner; control/task image identities accurately described. Run transport/unit/package tests and native timeout/failure/cleanup probes; preserve `--skip-build`. |
| **T7: move business development tests** | F5; selected tests/helpers → benchmark `tests/`; CLI/controls discovery, type/lint tooling | T6 first, or explicitly exclude local tests from the old control copy | Same aggregate cases executed, unique import identities, generic mechanism tests stay root, sentinel tests absent from every runtime payload/image. Run full checks, isolated benchmark test suites, packaging/distribution and native isolation controls. |

T4–T7 reduce maintenance without intentionally changing benchmark semantics. F8 is deliberately outside this immediate sequence. Freeze all source-manifest files before each guarded native run; report progress under `reports/`. Every accepted source change needs fresh controls before paid dispatch.

## 6. Architecture readiness

| Environment | Readiness | Concrete limitation |
| --- | --- | --- |
| Fixture-based | Existing invoice compile/execute/evaluate path and installed packaging are supported; local checks pass | F1 weakens paid-live preflight. F2 prevents treating a new normalized-only evaluator as fully integrated. |
| Docker-backed | Checkout uses the same worker/executor with benchmark `prepare`/`snapshot`, private Compose services and independent evaluation | Supported packaging is explicitly single-step Linux with separate verifier and CPU/memory limits. No fresh Docker run was performed for this audit. |
| Future Blindly/SimApp | No evidence demands another execution pipeline: an environment reachable through declared operations and `RunBinding` can use the existing flow | F2–F3 must be fixed for clean directory-only composition. If a concrete environment requires GPU/storage enforcement, multi-step author interaction, non-Linux execution or unsupported credential injection, `tasks.validate_config` currently rejects those requirements. No such Blindly requirements have been established here. |

**Recommendation: make the small corrective pass now, then run benchmarking experiments; do not start another architecture migration.** Existing unpaid invoice/checkout experiments can proceed with fresh controls. Fix F1 before relying on paid fixture preflight, and F2 before expanding benchmark coverage; align F3 when introducing configuration-driven hooks. Test relocation, transport cleanup, legacy deletion and lifecycle placement are maintenance work, not reasons to postpone all experiments. The passing 498-test check supports the migration's foundations; the two reproduced gaps justify targeted corrections rather than wholesale redesign.
