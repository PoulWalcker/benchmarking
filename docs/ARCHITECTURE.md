# Architecture

## Owners and flow

```text
native task -> author definition -> compile -> execute -> evidence -> evaluate
                  (optional)
```

Native Harbor tasks own operations, worlds and independent business evaluation.
Core supplies workflow mechanisms and research policy. External Harbor 0.21.0 owns
trial infrastructure: environments, collection, transfer, phase limits and cleanup.
A passing engine run does not imply acceptance; acceptance does not imply quality.
Unavailable facts remain null.

| Owner | Input | Process | Output |
| --- | --- | --- | --- |
| `tasks/<name>/` | public material, reference, fixtures or world observations | compose task behavior and independent evaluation | native task, observation plan and verdict |
| `compile/` | YAML, explicit bindings and trusted JavaScript | validate and lower workflow semantics | n8n artifact and step map |
| `execute/` | artifact and run binding | execute n8n, transport model calls, capture observations | immutable engine evidence |
| `verification/` | recorded evidence and independent callbacks | check provenance, obligations and optional rubric | acceptance and separate quality |
| `evaluate/` | recorded trial files | validate and normalize current/historical formats | recorded facts |
| `coordinate/` | selected tasks, source identities and budgets | compose stages, reserve calls and gate experiments | research report and trial references |
| `harbor_integration/` | native paths, YAML and wrapper requests | invoke Harbor and transfer protected submissions | authoritative Harbor trial directories |

Neutral contracts under `src/sapi_config_lab/*.py` import no stage. Independent
verification imports only itself. Task `evaluation/` and `environment/` modules
may import neutral contracts and verification, but not compiler/runtime or
coordination implementations. Their fixed `experiment.py` and `tests/main.py`
are composition roots and may call coordination mechanisms. Only integration
imports Harbor. `tests/test_boundaries.py` checks these edges, including literal
dynamic imports. It does not detect computed imports or business logic hidden in
file reads; those still require review.

## Native task ownership

`tasks/invoice-total/` and `tasks/checkout-recovery/` own their public instructions,
bindings, operations, private evaluation, source pins, calibration and oracle
solutions. Native `task.toml` owns Harbor configuration. Its small `metadata.sapi`
table contains research policy: default selection, model budgets, catalog arms,
admission mode and reference reward. It names no executable paths.

The task's fixed `experiment.py` composes exact authoring prompts, plans and current
offline evaluation for the host. The caller verifies the full source manifest and
executes this fixed file through isolated Python, then checks sources again. There
is no secondary descriptor, loader, registry, generated task directory or export
step. `tests/main.py` composes explicit task callbacks with
`coordinate/task_worker.py` inside the trusted verifier.

The shared worker runs prepare, plan, observe, snapshot and evaluate in order.
World preparation supplies a `RunBinding`; snapshots read the actual observation
or retain its absence. The worker validates the evaluator result and projects only
the task's declared reward. Invoice preflight executes its independent fixture
plan. Checkout preflight compiles only and leaves execution, acceptance and quality
null; successful admission is a separate selection eligibility fact.

## Images and submission boundary

`infra/native/Dockerfile` explicitly copies public and trusted assets into separate
image targets. `infra/native/build.sh` builds the runtime and four task bases from
the editable checkout; it creates no tasks or trials. Task Dockerfiles consume
these bases directly. The public ancestry contains only pinned runtime and public
material. Private fixtures, scoring and world code enter verifier images; references
enter only oracle solutions. Verifier import checks run during image builds.

The supported profile is single-step Linux/Docker, author UID 1000 and a separate
root verifier. `harbor_integration/task_config.py` validates it. The root collection
hook opens `/app/submission/config.yaml` without following links and rejects
executable, oversized, extra or non-YAML inputs. Harbor transfers the protected
snapshot to `/submission/config.yaml` after stopping the author. Conventional
`/logs/artifacts` collection is explicitly excluded; the verifier rejects injected
conventional artifacts. Missing YAML remains missing.

Author environments have no network. Checkout's verifier reaches its simulator on
an internal Compose network and the scoped host Agency bridge separately. Credentials
are scoped to a fresh world, never copied into the author context. Public image
layer and hostile-transfer controls check this placement.

## Runs, budgets and selection

Controls, generation and live replay use one `Run` resource owner. It creates a
fresh report and source manifest, pins direct task/input bytes and the exact public
and verifier image IDs, and checks those identities before dispatch and at close.
Image reuse requires a build record matching the complete current sources.
Documentation is included in that identity; progress belongs under ignored
`reports/` while guarded runs are active.

`Run.harbor` dispatches one selected task with one attempt and zero infrastructure
retries into `reports/<run>/jobs/`. Harbor's results remain authoritative. Research
reports add source/model/prompt identities, reservations, selection and compact
trial references, including partial trials. They do not reproduce Harbor's job
lifecycle. The report is finalized even when dispatch or cleanup fails; pre-existing
container identities are checked. The transport control uses the same argv builder.

Generation reserves a durable unknown ledger event before each single wrapper call.
There is no repair loop or fence stripping: exact valid answer bytes are uploaded.
Empty, oversized, failed or tool-marked answers cannot upload. Raw wrapper stderr is
not persisted. Selection always chooses the first-started attempt; a failed first
attempt is never replaced by a later passing one. Replay rechecks all recorded
hashes and normalized verdict authority and performs no generation call.

Live replay sums each admitted case's runtime grant and its task's judge cost, reports
that per-case allocation and refuses a larger total than `--max-calls` before any
reservation or native dispatch; a run's own ledger takes that allocation as its phase
ceilings. Grants bind operations, model and occurrence IDs. Native evidence is
reconciled against the exact selected YAML, rebuilt plan and compiled graph, ordered
case set and full file inventory. Runtime request/completion/response triples must
match input/output hashes, model, occurrence and timing. Runtime accounting closes
before judge reservation. Ambiguous runtime or judge timeout remains unknown and
blocks subsequent spending. A host-reserved judge forwards its exact pending event
to the task process without reserving a second call.

A fixture task's trusted composition root supplies `coordinate/fixture_judge.py` to
native evaluation; its evaluator receives only the Judge, never the host-owned endpoint,
and the native verifier records Judge identity without constructing one. Whichever
host command composed fresh fixture judging verifies the durable receipt against its
own reservation, model and inspection, and the quality against that reply, instead
of trusting the task process's success.

## Evaluation and historical reads

`evaluate/records.py` validates execution, acceptance and optional quality separately.
Controls check independent acceptance and task reward policy. Current native
re-evaluation requires complete `native-task.json` source/options/submission identity;
`native-sources.sha256` audits trusted container bytes against the image manifest.
The evaluator reads existing evidence and never starts n8n or a world. Saved replies
remain offline; explicitly requested fresh judging uses the same source-bound ledger.
Derived results sit beside immutable evidence. Original Harbor rewards and terminal
exceptions are preserved even when the host produces a later judged result.

Research calibration is a separate synthetic action of its composition root.
`evaluation/calibration.json` freezes six counterfactual final reports of the Orion
source, with expectations that never enter a Judge request. `calibrate` re-verifies one
source-matched native record, replaces only the final report the Judge reads, binds base
run digest, variant bytes and card into a new run digest, and reports native execution
and acceptance as null. Mocked replies compare as `simulated` and saved wrapper replies as
`replayed`; only a fresh dispatch in that call is `measured`. Label agreement from a
real Judge still needs human review of its reasons. Native evaluation refuses calibration.

Checkout controls use an explicitly labeled saved calibration adapter rebound to
each fresh run, plus unpaid fake runtime transport where needed. These scores are
calibration projections, not measured model quality. Protected real transport is
retained and tested with mocked wrapper responses; paid behavior is unverified.

Historical records remain readable without active task directories or descriptors,
including old hosted, lifecycle and fixture layouts. Missing native/terminal facts
stay null. Archived evaluator execution is deferred. Current native evaluation
requires the exact recorded source revision; switching to matching code is explicit,
not a snapshot-execution facility. Phase 1 native records without complete identity
remain readable but cannot claim guarded current re-evaluation. Committed historical
evidence is never rewritten.

## Compiler and refinement

`N8nBackend(operation_source)` requires explicit trusted JavaScript and bindings;
detached definitions inherit no business catalog. `WorkflowBackend` is the existing
engine seam; n8n remains its only implementation. A second implementation must bring
its own compiler, capability subset and native provenance before adding abstractions.

All existing profile validation and lowering remain, including bounded refinement
and admitted event syntax. `verification/refinement.py` independently checks native
attempt edges, carry, stopping, exhaustion, unchanged deadlines and model occurrence
identities using explicit independent expectations. Infrastructure retries do not
implement refinement. Lifecycle/WBS repair execution is deferred; retained profile
syntax and compilation do not provide a durable scheduler or repair controller.

## Timeouts and resources

| Owner | Limit | Meaning |
| --- | --- | --- |
| Native `task.toml` / Harbor | build, author, collection and verifier phases; CPU/memory | infrastructure limits with zero automatic retries |
| Task host planning | total observation ceilings against the verifier phase | reject oversized plans before dispatch or paid reservation |
| n8n executor | import/process ceilings and remaining absolute deadline | bound engine work without a second job watchdog |
| Task world | semantic window and one terminal snapshot | finalizer latency cannot extend workflow time |
| Agency / ledger | grant expiry, occurrence caps and reservations | bound and account for model work |
| Task evaluator | worker/judge timeout | bound recorded-evidence scoring |

Docker validation rejects unsupported storage, GPU and TPU requirements. The tested
privacy and resource claims apply to this pinned Docker profile, not every Harbor
provider. Forced termination preserves only observations actually written.

## Distribution and deferred capabilities

Ordinary wheels contain generic compiler/runtime/integration and independent verifier
code with their runtime resources. Detached compilation works with explicit bindings
and operations. Native task assets, generation prompts and image builds require the
editable checkout. Distribution checks install both direct and sdist-built wheels
outside the checkout and verify resource bytes and private exclusions.

The compact scope defers lifecycle/WBS repair, custom n8n UI, Markdown review export,
archived evaluator execution and task resource bundles in wheels. Their historical
implementations remain in Git. Current native offline evaluation/judging, normalized
historical reads and full compiler/refinement semantics remain active.
