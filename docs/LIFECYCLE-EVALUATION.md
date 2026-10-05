# Finite lifecycle evaluation

This experiment asks a model to author the complete `daily-digest` YAML from its public task, FORMAT, lifecycle extension, PROFILE and catalog. It uses the existing Harbor agent, native n8n backend, lifecycle controller and wrapper. The lifecycle controller remains an explicit component outside each exported candidate graph.

The immutable series admits these phases, in order:

| Phase | Origin | Maximum outgoing wrapper attempts |
| --- | --- | --- |
| authoring | One fresh model-authored YAML; no repairs or feedback | 1 |
| authored | Exact authored YAML: Callback test, exact revision release, actual wall-clock Cron | 2 digest summaries |
| mutation | A separately labeled copy with deliberately wrong `workflow.output`; genuine WBS forks, native tests, actual Cron | 4 digest summaries + 2 WBS requests |

The total ceiling is **9 wrapper attempts: 1 authoring + 8 runtime/rebuild**. Ceilings are reservations, not measured usage. Native requests, Agency audit records and WBS dispatch records establish actual attempts; provider-internal calls/retries remain unknown. There are no runner retries. An unfinished or failed phase stops the entire series, including after restart. A process lock excludes concurrent admission; each phase and outgoing grant is fsynced before dispatch.

First build the isolated lab image from the frozen source tree, following `infra/Dockerfile`. On the host, run:

```sh
python -m sapi_config_lab.experiments.lifecycle_run --series-dir /absolute/new-series author \
  --upstream http://127.0.0.1:8765/run --image sapi-config-lab-n8n:2.41.5
```

Before the paid call, the command pins the local image ID and runs fresh native Harbor oracle and nop controls. The oracle must match the host's runtime source inventory. Generation packages contain no reference YAML or solution. After freezing their prompt bytes, the command creates private article IDs and runs the returned YAML against two native Callback/Cron fixtures plus an engine-successful, business-rejected output mutation. Stub Cron uses an injected clock and does not claim wall-clock evidence.

Copy the complete frozen source tree and series directory into an isolated lab container; point `PYTHONPATH` and `SAPI_LAB_ROOT` at that copied tree. Preserve the directory between commands. No user n8n data or services are needed. Inside that container, run serially:

```sh
python -m sapi_config_lab.experiments.lifecycle_run --series-dir /probe/series live \
  --phase authored --upstream http://host.docker.internal:8765/run --cron-delay-seconds 180
python -m sapi_config_lab.experiments.lifecycle_run --series-dir /probe/series live \
  --phase mutation --upstream http://host.docker.internal:8765/run --cron-delay-seconds 900
```

Run the second command only after the first exits successfully. The driver also enforces this gate. Copy all series artifacts out after each command, including failures. These commands execute real models; local unit tests do not authorize calling them.

The generated original's inputs, 120-second execution deadline and daily schedule stay unchanged. Each phase records a separate controller schedule overlay at a fixed future UTC minute. The conservative mutation delay allows three candidate executions and two bounded rebuild requests before the due minute. The foreground wait is bounded, uses the actual system clock, and never installs a service. Missing the admitted minute fails the phase; it does not move the schedule or inject a clock.

`submission.yaml` preserves original returned bytes. `authoring/` holds prompts, private fixtures, controls and Harbor artifacts. `authored/` and `mutation/` hold separate registries, native artifacts, grant/audit files, snapshots and final reports. WBS candidates preserve their raw returned YAML and model identity. A repair of the intentional mutation is not counted as blank-slate authoring or as a change to the successful original submission.

The independent lifecycle verifier checks native business lineage, tested-revision release, fork/archive ordering and wall-clock admission. The driver reconciles each native model request with its own single-occurrence Agency grant and separately audits each configured-model WBS response. This finite evaluation does not prove arbitrary schedules, arbitrary catalog operations, or every crash interleaving.
