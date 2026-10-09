# Native Harbor Phase 1 parity

**Independently approved; no unresolved findings.** Branch
`migration/native-harbor-phase1` starts at
`2f84b02599417aa9a7bb41e8568bbaef34bf4151`; the reference spike is
`PoulWalcker/harbor-native-spike@00fffff8b7e410d06cc5b9f9e5db2e77003aa2c9`.
Approved code: `9bbfa4811d7ebada16faa0e2b4b858e77c51b03a`. Its only change from
verified runtime `4bd7d4c7ee7405fa45a4ae61924a9083d4daa84b` corrects the
network probe; task/compiler/runtime/evaluator/image sources are unchanged.

[Independent verification evidence](evidence/native-harbor-phase1/verification.json)
records exact commands, source/image identities, trial results and log hashes.
Its SHA-256 is
`474e131081cec8e11e7533f11d66c9ee5388865dac6182173d07fe8757ef12b8`.

## Changes and reuse

`tasks/invoice-total` and `tasks/checkout-recovery` are static Harbor 0.21.0 tasks.
`infra/native/build.sh` builds separate public/trusted images directly from existing
sources. Explicit native coordination roots call the shared `run_task` flow and
original benchmark entrypoints; no descriptor, loader, registry or materializer
was added. The validator, compiler, n8n executor, Agency bridge/reservation ledger,
submission gate, independent invoice evaluator, checkout world and scorer are
reused. Oracle YAML and public instructions are equality-tested native assets;
no domain implementation was copied. Legacy architecture remains operational.

## Actual independent verification

| Check | Result |
| --- | --- |
| Full `sapi-lab check` | Passed: 522 tests, 14 opt-in skips; Ruff/format, mypy, wheel/sdist checks |
| Native invoice oracle | Reward **1.0**; all 14 observations, comprising 11 real n8n 2.41.5 records and 3 intentional compile rejections |
| Invoice negative controls | Nop, incorrect/invalid YAML and unsafe submissions rejected; artifact smuggling excluded |
| Native checkout reference | Reward **0.732** on fresh world evidence using immutable saved simulated calibration; all four business checks passed |
| Runtime LLM bridge | Actual compiled n8n LLM nodes; two reserved deterministic fake calls succeed; timeout, failure, wrong-model and malformed replies fail closed |
| Budgets and nulls | Excess model budget rejected before dispatch; missing execution/quality facts remain nullable |
| Isolation and lifecycle | Public image layers/history/filesystems, credentials, mounts, private networks, HTTPS denial and Compose cleanup passed |
| Legacy and regressions | F1/F2, package security, checkout deadline/worker/evaluator/freshness faults passed; original dual-scenario `run.sh` passed oracle/nop and 19 real transport cases with `source_unchanged=true` |

The native matrix covers 19 logical cases across 19 initial trials and two corrected
isolation retests. Evidence retains the two initial TCP-probe test failures; both
corrected authenticated-TLS probes passed. This is collective verified coverage,
not a claim of one entirely green native-suite invocation. Existing container
identities were preserved, and no containers remained running at approval.
**No paid model or judge calls were made.**

## Functional differences and remaining risks

Native tasks require a checkout and explicitly rebuilt local base images before
Harbor's `--force-build`; source-distribution assets do not replace existing wheel
staging. Trusted task sources are hash-checked before verification, but this unpaid
path does not replace legacy paid-dispatch source/image gates or authoring, live,
selection, repair, cross-job budgets and historical re-evaluation workflows.

Checkout's calibration adapter verifies the saved reply's immutable hash and
contract, then projects its fixed answers into a **new simulated reply** bound to
fresh evidence. `calibration-transport.json` preserves original provenance and
records zero judge dispatches. Historical reply bytes remain unchanged; stale or
tampered replies are rejected. Neither calibration nor fake runtime transport
measures model quality.

Native authors use Harbor `no-network`; separate verifiers retain their own policy
and private simulator network. Missing/invalid checkout submissions are rejected
before workflow preparation, preserving null execution/quality. Missing scores
produce no reward file and Harbor reports `RewardFileNotFoundError`; this is not a
measured zero. Run instructions remain in [Development](docs/DEVELOPMENT.md#checked-in-native-harbor-tasks).

## Conditional Phase 2 deletion candidates

Only after **all** legacy consumers and identity/distribution guarantees migrate:

- `src/sapi_config_lab/harbor_integration/tasks.py`: descriptor-to-task packaging.
- `src/sapi_config_lab/coordinate/benchmark_packages.py` and `coordinate/packages.py`: legacy staging/composition.
- `src/sapi_config_lab/benchmark.py`, `benchmark_loading.py`, and `coordinate/benchmark_discovery.py`: descriptor/loading/discovery, after compile, authoring, live, evaluation and packaging consumers migrate.
- `benchmarks/*/scenario.json`, legacy `task.toml` and `verifier.sh`: replaced packaging declarations.
- `src/sapi_config_lab/coordinate/benchmark_worker.py` metadata-reading `main()` and `admit()`: retain shared `run_task` and execution/evaluation mechanisms.

Retain benchmark domain code, compiler/runtime, independent verification, world/scorer,
Agency security, normalized readers, evidence and experiment guard/ledger mechanisms.
Phase 2 has not started; nothing was removed, merged or pushed.
