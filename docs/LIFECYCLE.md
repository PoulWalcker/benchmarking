# Candidate lifecycle execution

Config 04 compiles into one complete n8n graph. Its finite attempt copies use
native IF continuation, carry the actual prior result/check feedback, and share
one workflow deadline. `sapi-lab compile` and `sapi-lab execute` use the same
backend; importing the export into n8n includes the full bounded refinement.
Checkpoint history survives rejected attempts and exhaustion. It is
workflow-authored evidence that the independent native verifier must check.

Config 05 is different: the durable controller manages candidate definitions,
tests, rebuilds and schedules around the native graph. A bare compile rejects
the lifecycle rather than dropping it. An admitted candidate export records
its exact Callback/Cron event and revision, but is not the entire controller.

## Explicit commands

These commands create an isolated project registry and perform real n8n
executions with deterministic operation stubs. They do not start user services
or install an operating-system Cron job.

```bash
python -m sapi_config_lab.coordinate.lifecycle --registry reports/my-lifecycle \
  register --config benchmarks/05-daily-digest/config.yaml

python -m sapi_config_lab.coordinate.lifecycle --registry reports/my-lifecycle \
  callback --workflow-id daily-digest --revision 1 --event-id initial-test

python -m sapi_config_lab.coordinate.lifecycle --registry reports/my-lifecycle status

python -m sapi_config_lab.coordinate.lifecycle --registry reports/my-lifecycle \
  serve --duration-seconds 180
```

Cron starts disabled and is admitted only after the candidate passes its
operational test. `serve` is a bounded foreground polling process; interrupting
it stops future polling. `tick` performs one actual wall-clock check. Only the
current scheduled minute is admitted; missed times are not replayed. A repeated
rule/event ID returns its stored outcome without another native execution.

For a wall-clock experiment at another time, registration accepts an explicit
`--schedule-overlay 'MINUTE HOUR * * *' --timezone Asia/Dubai`. The registry keeps
the original config/hash and the overlay separately. Pick a future local minute
and leave enough time for the test before starting `serve`. The Python
`tick(datetime)` seam is labeled `clock_source: injected`; it is useful for
deterministic tests and does not prove real Cron operation.

## Repair, limits and recovery

Live native operations require `--llm-mode live --bridge-url URL`. Real WBS
repair additionally requires the explicit `--rebuilder-url URL` pointing to the
existing wrapper. `--rebuild-model` checks its reported identity (default
`gpt-6-astra`); it does not select or override the wrapper's model. Options precede
the command verb. These options can make
paid calls; inspect the wrapper and freeze a finite experiment budget first.

The operational `digest.acceptance_v1` checks native success, preview mode,
nonempty summary and the supplied article IDs in order. It also requires one
prepare → summarize → preview source chain and the actual preview as output;
renamed step IDs are allowed. The experimental
verifier independently checks this result and native data lineage. A rejected
test queues a bounded rebuild. Without a configured rebuilder it remains queued;
the command reports incomplete work, never a successful replacement.

The original `max_rebuilds: 2` admits at most three tested candidates and two
rebuild requests. Each rebuild is one wrapper attempt, with no transport retry.
Raw returned YAML, prompt/hash, findings/source identity and dispatch outcome
are saved. A fork cannot change inputs, acceptance, execution/lifecycle policy
or activation rules. These limits do not include an operator's later Cron runs;
the experiment's outer admission ledger must bound those explicitly.

SQLite uses transactions and full synchronization. The exclusive runner lock
prevents two workers dispatching the same queued work. A completed durable
native result can finish its release transaction after restart. An uncertain
native or model result suspends the family and records Adhoc work; restarting
does not retry it. `drain` resumes only known queued/completed work.

`restore` restores an archived definition to draft without executing it or
changing the active schedule. The Python `register(..., forked_from=ref)` can
register an externally prepared new revision; its `registered_candidate` origin
is explicitly distinct from a model-authored rebuild. In-flight native runs
finish with their pinned revision before queued replacement tests execute.

`snapshot.json` contains immutable definitions, event admission and native
records, rebuild reservations, transitions, releases and Adhoc work. Native
artifacts live under `events/`; model repair evidence under `rebuilds/`. Existing
records and definitions are never rewritten to claim a new result. No Sapiens
harness, computer-use integration, Group/Lead process or publication is implied.
