# Phase 2 native Harbor migration

Implementation is complete for the user-approved compact scope. Independent final
functional/security and architecture verdicts are **pending**; the earlier A/B gates
passed before the compact removals. No paid model calls, push or merge were performed.

## Flow and ownership

Before: descriptor discovery → source/hook loader → package adapter → materializer
→ staged/copied task trees → Harbor → metadata-driven verifier dispatch.

Now: native `tasks/<name>/task.toml` and explicit Docker assets → guarded experiment
CLI → Harbor 0.21.0 → task-owned `tests/main.py` → generic compiler/n8n evidence
pipeline → independent task evaluation. Fixed task-owned `experiment.py` composes
host prompts, plans and current evaluation. It is source-checked before execution
and contains no plugin manifest or task registry.

Tasks own all domain assets. Harbor owns infrastructure, trial results and phase
limits. Research coordination retains identities, fixed selection and model budgets.
Job reports store compact references; cohort rows own derived verdict associations.
Original Harbor rewards/results and execution evidence remain unchanged.

## Removed and simplified

- Removed `benchmark.py`, `benchmark_loading.py`, `coordinate/benchmark_discovery.py`,
  `benchmark_authoring.py`, `benchmark_packages.py`, `packages.py`, and the
  `harbor_integration/tasks.py` materializer. Both task `scenario.json`,
  `legacy-task.toml`, `verifier.sh`, legacy checkout Compose/build fragments and
  orphan `harbor/templates/*` are gone.
- Removed `Run.stage`, temporary task trees, copied task-package evidence, staging
  bounds and descriptor branches. Harbor trials are read once; compact job references
  cannot be mistaken for duplicate scored rows.
- Retained only the existing narrow `validate_config` in `task_config.py` and
  `admit`/`run_task` in `task_worker.py`; removed metadata dispatch/dynamic imports.
  There is no replacement orchestration framework.
- CLI compile/list/build, source pins, tests and image builds use native assets
  directly. Duplicate derived-result writes were removed; the evaluator owns them.
- Removed the resource build hook and installed task bundle. Ordinary wheel checks
  still exercise detached compilation, exact runtime resources and private exclusions.

The detailed rename-aware file inventory is
[evidence/native-harbor-phase2/inventory.json](evidence/native-harbor-phase2/inventory.json).
Moves of unchanged assets count as zero code savings.

## Preserved and deferred scope

Preserved: existing compiler/profile syntax and lowering, bounded refinement with
independent verification, real n8n, real checkout state/credentials/deadlines, independent
invoice acceptance and quality, checkout scoring, Codex CLI wrapper, protected runtime
LLM transport, current offline evaluation and optional judge, budgets/unknown-outcome
latches, source/prompt/model identities, exact answer bytes and first-started selection.
Historical evidence and normalized readers remain intact without active descriptors.

Explicitly deferred by the user: lifecycle/WBS repair execution, custom n8n UI,
Markdown review export, archived evaluator execution (old and source-snapshot paths),
and benchmark task distribution through wheels. Removed APIs/commands include
`ui`, `lifecycle`, `review-export`, `package-tasks`, and evaluation snapshot options.
Their implementations remain in Git. Retained lifecycle syntax/event lowering does
not provide a durable controller. Current native evaluation requires the exact
recorded source revision; old incomplete identities remain readable, not executable
through a historical adapter.

No capability was removed to force a percentage target. The broad result is above
the planning range because required compiler, quality, refinement, transport and
research safety mechanisms remain.

## Code size

Comparable to approved Phase 1 `ce624339729444a167afaa3c12f2b40123d992b2`:

| Metric | Phase 1 | Pre-compact B | Current | Net vs Phase 1 |
| --- | ---: | ---: | ---: | ---: |
| Production/build/domain Python, JS and shell | 13,751 / 94 files | 14,905 / 100 | 10,820 / 81 | −2,931 (−21.3%) |
| Broad source/config | 14,725 / 120 files | 15,788 / 124 | 11,574 / 98 | −3,151 (−21.4%) |

Physical lines include blanks/comments. Narrow roots are `src`, `verification`,
`infra`, `tasks`, `benchmarks`. Broad adds YAML/TOML/Dockerfiles, root scripts/config
and `harbor`, `generation`, `public`; project tests, docs/prompts, JSON, evidence,
reports and vendored/calibration trees are excluded. Task `tests/` verifier entrypoints
are production. The inventory records exact predicates and every counted path.

Rename-aware narrow diff is **1,130 added / 4,061 removed**; broad is **1,170 added /
4,321 removed**. Net reduction is smaller than gross deletion because native guards,
identity and task composition were added. Explicitly deferred lifecycle/UI/archive
files account for 2,373 baseline lines (2,667 at B); the removed wheel build hook
adds 28 baseline lines (39 at B). Thus baseline narrow savings include 2,401 lines of
scope deferral and 530 net other changes; broad savings include those 2,401 and
750 net other changes. The latter includes consolidation and edits to retained
packaging/CLI mechanisms, not pure infrastructure savings. Tests/docs/moves are not
included in either code reduction.

## Verification

- Stage A independent approval `852b8c2f`: native installed controls at that then-supported
  boundary, all 14 invoice observations, checkout 0.732/all four checks, image placement,
  source guards and cleanup. Installed-task capability was later explicitly deferred.
- Stage B retained-core approval `0abdfd64`: full check (562 tests), transport, both
  oracle/nop controls, 17 native trials, all five faults, fresh offline evaluation and
  real Harbor with mocked judge dispatch. No paid calls; original Docker baseline preserved.
- Compact C implementation check: `uv run --locked sapi-lab check` passed with 434 tests,
  10 opt-in Docker skips, lint/format/types and actual direct/sdist wheel installations.
  Seven runtime/verifier resources and 11 private exclusions passed.
- Reader correction `aa95dd2` / `df05bed`: 29 focused tests passed. Exactly one derived
  row is exposed; missing/malformed explicit verdicts fail closed; partial jobs remain
  discoverable; original reward/evidence and historical reads are preserved.
- Final independent native controls, fault/security matrix, offline/live mocked dispatch
  and architecture re-review: **pending frozen-commit verification**.

Raw logs remain under `reports/phase2/`. Final branch-accessible independent verdict
summaries will be retained under `evidence/native-harbor-phase2/` after completion.
Real paid transport and model performance remain unverified; control calibration
scores are explicitly saved-answer projections. The supported isolation/resource
claims apply to the tested Harbor 0.21.0 Docker profile.
