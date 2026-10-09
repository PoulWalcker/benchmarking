# Native Harbor Phase 1 parity

Integration branch: `migration/native-harbor-phase1`, based on
`2f84b02599417aa9a7bb41e8568bbaef34bf4151`. Reference spike:
`PoulWalcker/harbor-native-spike@00fffff8b7e410d06cc5b9f9e5db2e77003aa2c9`.

## Changes and reuse

Two static native Harbor 0.21.0 task directories now live under `tasks/`.
`infra/native/build.sh` builds explicit public/trusted image targets directly from
original sources. No task loader, descriptor, registry or materializer was added.
The original worker exposes its unchanged observation/evaluation flow as
`run_task`, shared by the manifest worker and native task entrypoints. Existing
validator, compiler, n8n executor, Agency bridge/reservation ledger, submission
admission, invoice fixtures/evaluator, checkout world/hooks and scoring are reused.
No benchmark domain implementation was copied. Oracle YAML and original public
instruction bytes are duplicated only as native Harbor assets and equality-tested.

## Actual verification

Independent acceptance is pending. First independent review requested corrections
to native coordination ownership, saved calibration on fresh evidence, and the
author network policy. Those corrections are implemented; the final native and
security gate remains pending. Prior implementation results (before corrections):

- `uv run --locked sapi-lab check`: full automated checks passed (520 tests, 14 opt-in skips,
  Ruff/format, all mypy namespaces, isolated wheel/sdist installation and staging).
- Invoice native oracle: reward `1.0`; all 14 original observations recorded with
  real n8n 2.41.5 (`reports/native-phase1/jobs/invoice-total-2f7f2850/`).
- Checkout native reference: reward `0.732`, independent stateful-world checks and
  saved-reply replay passed (`reports/native-phase1/jobs/checkout-recovery-6f19108a/`).
- Native compiled n8n LLM nodes and the existing Agency bridge passed with two
  deterministically faked, reserved calls and reward `0.732`
  (`reports/native-phase1/jobs/checkout-recovery-2dc73cd0/`).
- The prior checkout result used a fresh demo response, so it did **not** prove
  the requested saved-calibration route on fresh native evidence.
- Saved original calibration replay passes against the unchanged scorer. Source
  identities remain in `tests/native/calibration/origin.json`; saved bytes came
  from the pinned spike, originally archived at its recorded baseline commit.
- Correction round: 27 targeted tests pass, including fresh-evidence saved-answer
  adaptation to `0.732`, immutable template verification, stale-reply/tamper
  rejection, missing-evidence nulls, native ownership boundaries and F1/F2.
- The exhaustive native/security/failure suite is implemented but awaits the
  independent verifier. Logs, failed development attempts and successful controls
  remain under `reports/native-phase1/`; no failed attempt is treated as parity.

Commands and test scope are in `docs/DEVELOPMENT.md`. Native control identity
records contain exact argv, host source hashes and public/verifier base image IDs.
Each trusted task image freezes its complete `/tests` tree plus submission gate
in `/opt/native-sources.sha256`, verifies those hashes before execution and saves
the inventory beside trial evidence. Local tags require an explicit rebuild;
this is not the legacy guarded paid-dispatch image-identity policy.

## Functional differences and remaining risks

Native tasks require this checkout and explicitly built local image tags; rebuild
bases before Harbor's `--force-build`. Native assets ship in the source distribution;
existing wheel staging remains the installed-resource path. The native adapter is
an unpaid parity/control path: checkout runtime LLM transport is deterministic fake
transport. A hash-pinned saved simulation reply supplies only fixed answer values
to an explicit deterministic calibration adapter. Its NEW simulated reply carries
fresh run/response hashes; `calibration-transport.json` preserves original source
identity/provenance and records zero judge dispatches. Historical reply bytes are
unchanged and cannot pass scoring against fresh evidence. It makes no model-quality
claim and does not replace legacy guarded paid authoring, live dispatch, selection,
repair, cross-job budgets or historical re-evaluation.

Checkout rejects missing/invalid submissions before preparing a workflow window;
its execution and quality remain null and no Harbor reward file is written.
Harbor represents that missing reward as `RewardFileNotFoundError`. A missing judge
also remains unscored. The author environment now explicitly uses Harbor
`no-network`, unlike the legacy default public network; the separate verifier
retains its own network policy and private simulator network. Invoice keeps its full original 14-observation plan (including
three positive and seven invalid-input business cases plus four verifier probes).
No old benchmark architecture is removed in this phase.

## Phase 2 deletion candidates

After migrating **all** legacy callers and their identity/distribution guarantees:

- `src/sapi_config_lab/harbor_integration/tasks.py`: descriptor-to-task packaging.
- `src/sapi_config_lab/coordinate/benchmark_packages.py`: legacy positive staging.
- `src/sapi_config_lab/coordinate/packages.py`: legacy task composition/selection.
- `src/sapi_config_lab/benchmark.py`, `benchmark_loading.py`, and
  `coordinate/benchmark_discovery.py`: descriptor/discovery/loading, only after all
  compile, authoring, live, evaluation and packaging consumers migrate.
- `benchmarks/*/scenario.json`, legacy `task.toml` and `verifier.sh`: metadata and
  shell packaging declarations replaced by static native task directories.
- `coordinate/benchmark_worker.py` metadata-reading `main()` and `admit()` adapter:
  retain the shared `run_task` composition and its execution/evaluation mechanisms.

These are conditional candidates, not evidence that deletion is safe today.
Keep benchmark domain source, compiler/runtime, independent verifier, world/scorer,
Agency security, normalized readers, legacy evidence and experiment guard/ledger
mechanisms. No merge or push is part of this change.
