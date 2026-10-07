# Architecture

## One flow

```text
definition -> COMPILE -> artifact -> EXECUTE -> evidence -> EVALUATE -> verdicts
     ^
     |
  AUTHOR (optional)
```

`coordinate/` selects scenarios and cases, binds fixtures, enforces budgets, stages Harbor task packages and sequences the stages. Harbor isolates each trial; it is orchestration around the flow, not part of it. The boundary that matters is **definition -> artifact -> evidence -> verdict**.

## Stages

| Stage | Location | Owns | Never |
| --- | --- | --- | --- |
| Shared | `src/sapi_config_lab/*.py`, `bindings.yaml` | profile validation, backend contracts, evidence encoding, pinned sources, wrapper stderr audit | import a stage |
| Author | `author/` | model-written or repaired YAML | compile or score it |
| Compile | `compile/` | validated YAML -> n8n JSON + step map | start n8n or decide acceptance |
| Execute | `execute/` | n8n runs, Agency bridge, the hosted trial host and environment providers, Harbor/Docker host tools and host configuration | turn engine success into acceptance |
| Evaluate | `evaluate/` | scoring recorded evidence (hosted provider evaluators, lifecycle test decision, review export) | rerun a workflow |
| Coordinate | `coordinate/` | CLI, scenario registry, runs, ledger, packaging, live gates, lifecycle controller | hold stage logic another stage owns |
| Verifier | `verification/` | independent acceptance and rubric | import compiler, runtime or coordinator code |

`tests/test_boundaries.py` enforces these import edges. Nothing imports `coordinate/`.

## One scenario registry

Every benchmark is `benchmarks/NN-<name>/`. Its `scenario.json` states what the scenario needs; there is no second registry in code.

| Field | Meaning |
| --- | --- |
| `environment` | `fixtures` (verifier-planned cases in the container) or a hosted provider name (`autowfbench`), served by the host per trial |
| `evaluator` | `verifier` for fixtures, otherwise one the environment's provider entry allows (`autowfbench`); checked on load |
| `default` | selected when a command gets no `--scenario` |
| `provenance` | AutoWFBench config: `{source, challenge}` of a pinned upstream task; `provenance/<source>-source.json` pins its bytes |
| `workflow_id` | the workflow id an author must use (hosted tasks) |
| `bindings` | a scenario-owned operation catalog instead of `src/sapi_config_lab/bindings.yaml` |
| `budgets` | `authoring_attempts` (0 refuses authoring), `runtime_model_calls` |
| `output` | `artifact_field`/`artifact_name`: an output submitted verbatim as a named file |
| `controls` | `reference_reward` the oracle must reproduce |
| `prompt_extension`, `fresh_fixtures`, `human_review` | authoring prompt section, per-run fixture overlay, live report flag |
| `harbor` | trial resources rendered into `task.toml`; a verifier timeout must contain the verifier's plan |

Files beside it: the reference `config.yaml`, the container `instruction.md`, and either `task.md` + evaluator-only `cases.json` and `evaluation/` (`contract.json`, optional `rubric.json`) (fixtures) or `authoring-notes.md` + `bindings.yaml` (hosted). Scenario-owned evaluation facts live in `evaluation/` as data; the mechanisms that read them stay in `verification/`.

Where a benchmark came from is provenance data. It selects which pinned bytes are trusted, never which code path runs; the code path follows `environment` and `evaluator`.

## One run mechanism

Every Harbor experiment (`harbor`, `generate`, `live`) runs through `coordinate/runs.py`:

- a new report directory, a source manifest, a pinned base image identity and staged task packages;
- each phase re-checks sources, image and pinned inputs;
- `Run.harbor` serves hosted tasks: a `TrialHost` per task on the host, credentials only in a per-job copy, and a leak scan over everything persisted;
- `Run.bridge` runs the budgeted Agency bridge;
- the report is always written, cleanup errors included.

Paid work is reserved in `coordinate/ledger.py` before it starts; an unknown outcome is never released.

## Hosted providers

A scenario has one of two placements (`scenario.hosted`): in the container (fixtures and the independent verifier) or served by the host per trial (any other `environment`).

- `execute/hosting.py` is provider-neutral. `TrialHost` is the trusted trial endpoint: `/begin` and `/finish` behind a per-trial token, a deadline timer, and the evidence files `environment-evidence.json`, `transport-evidence.json`, `native-record.json` and `trial.json` (which names its scenario). It drives one `EnvironmentSession` per trial, a Protocol: `connection`, `limit_seconds`, `identity`, `finalize()`, `transport_evidence()`, `close()`.
- `coordinate/providers.py` is the one place names become provider code. `ENVIRONMENTS[name]` is a `HostedEnvironment` (allowed `evaluators`, `limit_seconds`, public `task` text, `start` returning a session, host-only `modules`); `EVALUATORS[name]` is a `HostedEvaluator` (`judge_calls`, `prepare`, `reevaluate`, `modules`). `host_only_modules()`, the generic host modules plus every provider's, is scrubbed from hosted containers.
- A provider owns its world (starting and seeding it, its tool listener, `finalize()` evidence, limits, identity) or the scoring of its recorded evidence, and reads its own scenario config. The core (`runs.py`, `packages.py`, `evaluation.py`, `controls.py`, `live.py`, `scenarios.py`) owns the run lifecycle, staging, budgets, ledger, leak scan, result shape and control rules.
- AutoWFBench is one provider: `execute/autowfbench.py` (the pinned upstream world behind a candidate tool listener with receipts and an ambiguity policy) and `evaluate/autowfbench.py` with `judge_calibration.py` (its pinned upstream scorer and judge). It reads `provenance`.

`tests/test_boundaries.py` keeps the seam: provider modules are imported only by coordination and by each other; core coordination modules other than `providers.py`, and the shared modules, never import or name a provider; a test-only fake provider stages a package and runs a real `TrialHost` trial without editing `src/`.

## Evaluation

`coordinate/evaluation.py` gives every evaluator one result shape: execution, acceptance and optional quality (`null` is not zero). Control rules branch only on hosted versus verifier.

- **Verifier** (`verification/`, plus the scenario's `evaluation/` data packaged beside it): `plan` states which definitions must run, `coordinate/observe.py` runs them and records evidence, `evaluate` checks the record is exactly that plan and judges it. It is packaged into each task, and deliberately duplicates business rules instead of importing the implementation under test.
- **Hosted evaluators** (`EVALUATORS`): run on the host against a trial's recorded evidence; `hosted_evaluation` dispatches to the scenario's evaluator and writes `evaluation/report.json`, and `sapi-lab evaluate` re-evaluates a recorded trial through the same entry. AutoWFBench's pinned upstream scorer and judge are one hosted evaluator.

## Evidence

Execution evidence is written once; evaluation writes separate derived files beside it. `reports/` holds local runs (ignored). `evidence/` holds committed extracts that durable claims cite; they are never rewritten.

Pinned inputs are explicit and fail closed: the upstream source manifest under `provenance/`, the Harbor and n8n versions in `execute/`, the image identity, the source manifest of a run, prompt hashes in `tests/test_packaging.py`, and the inspected model-wrapper identity before live dispatch.

## Backend boundary

`WorkflowBackend` (`contracts.py`) is the seam between workflow semantics and an engine; n8n is the only implementation. A second backend must bring its own capability subset, compiler, native evidence and provenance checks. Do not add a universal IR, registry or inheritance tree before that second implementation exists.

## Terms

| Term | Meaning |
| --- | --- |
| definition | validated `sapi-lab/v0` workflow YAML |
| artifact | compiled backend workflow (n8n JSON) |
| execution | one engine run and its native evidence |
| acceptance | independent pass/fail verdict |
| quality | optional rubric or judge score |
| oracle / nop | positive control (reference submission) / negative control (no submission) |
| scenario / case | one benchmark directory / one fixture input of it |
| hosted | a scenario whose `environment` is a provider, not `fixtures`: the host serves its world and runs its evaluator per trial |
