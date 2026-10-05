# Glossary

This project reuses a small number of words for several unrelated things. The
collisions are real and they are the main reason a newcomer misreads a report.
This document fixes one meaning per word, lists every other meaning currently in
the tree with the file and line that proves it, and names what each other
meaning should eventually be called.

Every claim below was checked against the source in this checkout. Where a
rename is proposed it is marked **safe** or **blocked**; a blocked rename is one
where the word is a field in a report schema that is already recorded in
immutable evidence directories. Those schemas are listed under
[Frozen schemas](#frozen-schemas). Renaming a frozen schema field is forbidden,
so for those terms only the prose, the variable names and the CLI flags can
change.

## Three facts the rest of the documentation assumes

### 1. Execution success, acceptance and quality are three separate facts

They are recorded as three different things and must never be collapsed into
one "it worked".

- **Execution success** means the engine imported the graph, ran it, and
  produced its required runtime result. It is `execution.succeeded` in
  `sapi-lab-execution/v1` (`src/sapi_config_lab/runtime/execution.py:95-103`).
  It says nothing about the business answer.
- **Acceptance** means the independent verifier, which the runtime never
  imports, checked business criteria *and* native execution provenance and
  recorded a decision. It is `acceptance.passed`, which stays `null` until that
  decision exists (`runtime/execution.py:104`, `verification/verify.py:63`). A
  deliberately corrupted workflow can have `execution.succeeded: true` and
  rejected acceptance; the negative test then passes because rejection was the
  expected behavior.
- **Quality** means a judgement that the produced text or artifact is good. On
  the benchmark track that is the separate semantic judge
  (`src/sapi_config_lab/runtime/task_evaluation.py:342`), scored out of ten.
  On the project's own nine scenarios there is no automatic quality score at
  all: prose checks are lexical coverage checks, and human review is tracked as
  `human_review: pending`.

A graph that runs, passes acceptance, and produces a weak answer is a normal and
fully recorded outcome. See the [report field guide](REPORTS.md) for the field
layout.

### 2. oracle and nop are two controls of one run, not a test run and a real run

Every control suite runs the same tasks twice in the same run: once with the
reference solution supplied (`oracle`) and once with no solution supplied
(`nop`). The oracle trial must score 1 and the nop trial must score 0. Both
assertions are made in the same block, for the same task set, in
`src/sapi_config_lab/experiments/harbor.py:163-193`.

If the oracle trial does not score 1, the measuring instrument is broken: the
image, the transport, the packaging or the verifier. If the nop trial does not
score 0, the verifier accepts something it should reject. Neither outcome says
anything about the task being measured, and neither is a reason to rerun and
hope. `generate` refuses to dispatch a paid authoring call at all when the
control suite fails (`experiments/generation/run.py:224-227`).

### 3. Harbor's reward stays binary for the project's own nine scenarios

For the nine scenarios in `src/sapi_config_lab/core/scenarios.py`, the packaged
`test.sh` writes exactly `1` or `0` to `/logs/verifier/reward.txt`
(`harbor/templates/test.sh`), and every runner asserts exactly 1.0 or 0.0:

- `src/sapi_config_lab/experiments/harbor.py:185,190`
- `src/sapi_config_lab/experiments/generalization.py:171`
- `src/sapi_config_lab/experiments/lifecycle_run.py:289`
- `src/sapi_config_lab/experiments/replay.py:81,206` (historical and stub trials,
  which must have recorded `{"reward": 1.0}`)

The continuous `score/10` reward belongs only to the separate AutoWFBench
benchmark track, where it is `normalized_reward = score_0_10 / 10`, computed in
`Decimal` from the same quantized value as the score
(`experiments/task_evaluation.py:34,418`). The two are never pooled.

## Frozen schemas

These schemas are written into evidence directories that are never rewritten.
Their field names cannot be renamed.

| Schema | Written by | Field names that collide with glossary terms |
| --- | --- | --- |
| `sapi-lab-execution/v1` | `src/sapi_config_lab/runtime/execution.py:95` | `execution`, `acceptance`, `input.activation` (holds a lifecycle admission), `llm`, `evidence` |
| `sapi-lab-verification/v1` | `verification/verify.py:204` | `cases`, `case_count`, `scenario`, `submission_sha256`, `passed` |
| `sapi-lab-task-evaluation/v1` | `src/sapi_config_lab/runtime/task_evaluation.py:22,412-420` | `normalized_reward`, `project_acceptance.criterion`, `evaluation_mode` |

Harbor's own trial record carries `rewards: {"reward": <float>}`. That is
upstream's schema, not ours, and is equally out of reach.

## Ambiguous terms

### admission

**Standardise on:** *admission* = a durable record that one specific thing is
allowed to happen exactly once. Every current use fits that shape; the problem
is that four different things are being admitted and the word alone does not say
which.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| **Lifecycle event admission.** The controller admits one Callback or Cron event against one workflow revision. The admitted document is `{kind, rule_id, event_id, workflow_ref, purpose}`. | `src/sapi_config_lab/runtime/lifecycle.py:339-368` creates it; `runtime/n8n/compiler.py:65-79` validates exactly those five fields; `runtime/n8n/refinement.py:20,99-104` only forwards the same object into the refinement graph's context | **event admission** | **Blocked.** It is persisted as `admission` in the controller's event records, checked field-by-field by `verification/lifecycle.py:153-171`, and copied into `sapi-lab-execution/v1` as `input.activation` |
| **Outgoing occurrence admission.** The Agency budget names, up front, every model-call occurrence a run may make, and refuses any invocation not on that list. | `src/sapi_config_lab/runtime/agency.py:153-169` ("Invalid outgoing occurrence admission"), enforced at `agency.py:210-222` | **call grant** | **Safe.** These are in-process validation messages and a local `budget.json`, not a published schema |
| **Granting a UI model-call budget.** `admit()` writes a one-time `budget.json` for one prepared graph. | `src/sapi_config_lab/experiments/ui.py:131` | **grant** (it constructs the call grant above) | **Safe** |
| **Series reservation.** An expansion or refinement series reserves one bounded attempt before launching it and closes the reservation from the observed outcome. | `src/sapi_config_lab/experiments/expansion.py:1,196` ("Unknown expansion admission"); `experiments/lifecycle_run.py:98` | **reservation** — the module's own prose already uses that word | **Safe** |
| **"Durable admission" as a missing feature.** The catalog notes that it is not implemented. | `src/sapi_config_lab/workflow/bindings.yaml:215` | **durable retry/admission (not implemented)** — keep as prose, it is a limitation note | n/a |

Audit correction: `runtime/n8n/refinement.py:20,99-104` is **not** an acceptance
rubric injected into a refinement graph. It is the same lifecycle event
admission document, forwarded unchanged; `compiler.py:67` asserts its field set
is exactly `{kind, rule_id, event_id, workflow_ref, purpose}`. There is no
rubric anywhere in the refinement path. Separately, the series-reservation
meaning above is a real fifth use that the audit did not list.

### oracle

**Standardise on:** *oracle* = the trusted reference solution supplied as the
positive control of a run. It is never a grader.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| Harbor agent that copies the reference YAML in as the submission | `harbor/templates/solve.sh`; `--mode oracle` at `src/sapi_config_lab/cli.py:131`; `experiments/tasks.py:30` | **oracle** (keep) | n/a |
| `oracle_config(contract)` builds a scripted reference configuration for the benchmark tasks | `tests/support/checkout_oracle.py:1,9`, `tests/support/crm_oracle.py:1,9`, loaded at `experiments/checkout.py:128-134` | **oracle** (keep) | n/a |

Audit correction: these two are **not** opposite roles. `checkout_oracle.py` and
`crm_oracle.py` do not grade anything — their own docstrings call them "trusted
scripted calibration" and "trusted native CRM control", and they return a
workflow configuration. `experiments/checkout.py:572` runs that configuration as
the `reference` trial against a `nop` trial, which is exactly the oracle/nop
pair. The graders in this project are `verification/verify.py` (acceptance) and
`task_evaluation.judge()` (quality); neither is called an oracle. The word is
already consistent and should stay.

### case

**Standardise on:** *case* = one input fixture executed once and judged once.
That is the verifier's unit of work.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| A verifier test input and its expected outcome | `verification/cases.json`; iterated at `verification/verify.py:235-266` | **case** (keep) | n/a |
| The per-execution record produced by running one case | `src/sapi_config_lab/runtime/execution.py:30` (`run_case`), written to `case.json` at `execution.py:123` | **execution record** in prose; the file name stays | **Blocked** for the artifact name and `cases`/`case_count` in `sapi-lab-verification/v1` |
| `--case` of `benchmark-calibrate`: one of three frozen calibration narratives | `src/sapi_config_lab/experiments/judge_calibration.py:135` | **`--control`** — these are evaluator-only controls, not inputs | **Safe**, it is a CLI flag with three fixed values |
| A whole AutoWFBench challenge, as in "the single-case Checkout Recovery evaluation" | `README.md:20`; `docs/CHECKOUT-EVALUATION.md:73` | **challenge** | **Safe**, prose only |

### task

**Standardise on:** *task* = a Harbor package: a directory with an instruction,
an environment, a verifier and a test script, built by `stage_tasks()`.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| Harbor package | `harbor/tasks/<scenario>/`, assembled by `src/sapi_config_lab/experiments/tasks.py:15` | **task** (keep) | n/a |
| A natural-language prompt handed to an authoring model | `generation/tasks.json` | **prompt** | **Safe**, but the file name is referenced by `experiments/tasks.py:53` and by recorded source manifests, so rename the concept in prose and leave the file |
| An AutoWFBench challenge plus its local wiring | `src/sapi_config_lab/core/benchmark_tasks.py:7` (`TaskDefinition`), `--task crm` | **task integration** | **Blocked** for `sapi-lab-task-definition/v1` (`benchmark_tasks.py:21`) and `sapi-lab-task-evaluation/v1`; the `--task` flag could change but the churn is not worth it |

### control

**Standardise on:** *control* = a trial whose expected result is known in
advance, run to check the instrument rather than the subject.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| The oracle and nop trials of a run | `src/sapi_config_lab/experiments/harbor.py:163-193` | **control** (keep) | n/a |
| `--mode controls`: an unpaid pre-run gate on the benchmark track | `src/sapi_config_lab/experiments/checkout.py:459,571` | **`--mode gate`** would be clearer, but it is also literally a control run | **Safe**; low value, leave it |
| Hand-written reference DAGs used as the positive controls of the composition experiments | `generation/generalization/controls/*.yaml`, selected at `experiments/generalization.py:61` | **control** (keep) — they are the oracle solutions of those two tasks | n/a |
| The lifecycle **controller** daemon | `src/sapi_config_lab/runtime/lifecycle.py:169` (`LifecycleController`) | **controller** — a different word already; never shorten it to "control" | n/a |

Audit note: `--mode controls` belongs to `benchmark` (and its deprecated
`checkout` alias) at `experiments/checkout.py:459`, not to `generate`. `generate` has no `--mode`;
it always runs the control suite first and aborts if it fails
(`experiments/generation/run.py:224-227`).

### candidate

**Standardise on:** *candidate* = the thing being measured, as opposed to the
reference. Both current senses are that; they differ only in what is being
measured.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| The registered workflow revision under a lifecycle controller | `src/sapi_config_lab/runtime/lifecycle.py:1-4`, `experiments/lifecycle_run.py:496-507` (`candidate.yaml`, `candidate_sha256`) | **candidate revision** | **Blocked** where it is a key in the controller's durable records and in the lifecycle report (`lifecycle_run.py:496`) |
| The model's solution, scored against the scripted reference | `src/sapi_config_lab/experiments/checkout.py:251,590-603` | **candidate** (keep) | n/a |
| Local variable for filesystem search paths | `src/sapi_config_lab/paths.py:16` | **search_paths** | **Safe**, it is a local variable; it is not the domain term and should not be read as one |

### reward

**Standardise on:** *reward* = the number Harbor collects for a trial. Always
state which track produced it.

| Meaning in use | Proof | Should be called | Rename |
| --- | --- | --- | --- |
| Binary suite pass for the project's nine scenarios | `harbor/templates/test.sh`; asserted at `experiments/harbor.py:185,190`, `generalization.py:171`, `lifecycle_run.py:289`, `replay.py:81,206` | **suite reward** | **Blocked**, Harbor's `rewards.reward` |
| Continuous `score_0_10 / 10` on the AutoWFBench track | `src/sapi_config_lab/runtime/task_evaluation.py:139,418` | **normalized score** | **Blocked**, `normalized_reward` is a `sapi-lab-task-evaluation/v1` field |

A binary reward answers "did the whole suite pass". A `score/10` answers "how
good was this one answer". Comparing or averaging them produces a meaningless
number.

### binding and catalog

**Standardise on:** *catalog* = the set of operations a workflow may use;
*binding* = one entry in it. The two words already name the same object and the
project is split roughly in half.

| Meaning in use | Proof |
| --- | --- |
| `workflow/bindings.yaml` is the single source operation catalog; its own first line calls itself a "Logical catalog" and its top-level key is `operations:` | `src/sapi_config_lab/workflow/bindings.yaml:1,8`; `docs/ARCHITECTURE.md` says the same |
| The constant that points at it is `CATALOG` | `src/sapi_config_lab/paths.py:6` |
| The benchmark track's per-task operation file is named `*-bindings.yaml` but the field selecting it is `catalog` | `src/sapi_config_lab/core/benchmark_tasks.py:10`; files `generation/crm-bindings.yaml`, `generation/checkout-bindings.yaml` |
| Runtime parameters and prose use `catalog` throughout | `src/sapi_config_lab/runtime/agency.py:245,257-259` |

Proposed rule: **catalog** for the collection, **binding** for one operation's
entry inside it. The file names (`bindings.yaml`, `*-bindings.yaml`) are hashed
into recorded source manifests by `experiments/provenance.py`, so renaming the
files is **blocked** in practice; renaming the prose and new identifiers is
**safe**.

Audit correction: `generation/crm-bindings.yaml` has **no** field named
`catalog`. Its top-level key is `operations:` (line 1), exactly like
`src/sapi_config_lab/workflow/bindings.yaml`. The field named `catalog` is
`TaskDefinition.catalog` at `experiments/benchmark_tasks.py:10`, a string that
points at those files.

## Unambiguous anchors

These words have one meaning. They are listed because they are the vocabulary
the ambiguous terms are defined against.

- **scenario** — one of the nine named task families in
  `src/sapi_config_lab/core/scenarios.py`, each mapped to one file in `configs/`.
  `scenario` is a field of `sapi-lab-verification/v1`.
- **challenge** — an upstream AutoWFBench problem, identified by
  `challenge_id` (`experiments/benchmark_tasks.py:9`). Only two are pinned:
  `production-checkout-recovery` and `crm-lead-qualification`
  (`runtime/autowfbench.py:152`). A challenge is not a scenario.
- **trial** — one Harbor execution of one task by one agent, collected as a row
  by `load_trials()` (`experiments/harbor.py:37-49`): task name, rewards,
  exception, result path, acceptance.
- **job** — a named group of trials inside Harbor's `--jobs-dir`, selected with
  `--job-name`. The control suite uses one job per agent, so the jobs are
  literally named `oracle` and `nop` (`experiments/harbor.py:160-180`).
- **observation** — `WorkflowObservation` in `verification/contracts.py:8`: the
  logical values a workflow produced (`final`, `events`, `states`, `roles`),
  deliberately engine-neutral. Its own docstring states that the object alone
  does not establish execution provenance.
- **role contract** — `RoleContract` in `verification/roles.py:20`: the
  independent statement of which operation each logical step must perform, what
  its inputs must originate from, and what its dependencies must be. It binds
  roles, not step IDs, so a submission may rename its steps.
- **acceptance** — the independent verifier's decision that a result satisfies
  the business criteria *and* the native provenance checks. It is both a field
  of `sapi-lab-execution/v1` and a free-text field of the YAML profile
  (`workflow.acceptance`, validated at `workflow/profile.py:254`); the YAML
  field is the author's statement of intent and is never read as a decision.
- **evaluation** — one scored benchmark trial, written once to
  `evaluation.json` and immutable thereafter (`task_evaluation.py:423-445`).
- **criterion** — one scored line of an upstream scorecard, with its own weight
  and cited evidence. Four deterministic criteria are worth six points and three
  semantic criteria four points for the checkout challenge.
- **judge** — the separate upstream semantic grader invoked by
  `task_evaluation.py:342`. It has its own prompt and subprocess, it costs a
  real model call, and a missing or invalid judgement leaves a trial *unscored*,
  never zero.
- **nop** — the Harbor agent that writes no solution. Its trial must score 0.
- **occurrence** — one admitted model-call slot, identified as
  `scenario/r<N>/<operation>[/attempt<N>]` and usable exactly once
  (`runtime/agency.py:155-160,210-222`). The authoring prompts also use
  "operation occurrence" for one appearance of an operation in a DAG
  (`generation/tasks.json`); the two line up, because one graph occurrence is
  what gets one call slot.
- **profile** — `sapi-lab/v0`, the closed subset of the Sapiens specification
  this project implements (`workflow/profile.py:1`, pinned at `SPEC` on line
  11). Not a performance profile, and not the whole specification.
- **operation** — one named unit of work in the catalog, with declared inputs,
  outputs and a `kind` of `Script` or `LLM`.
- **submission** — the YAML a solver put at `/app/submission/config.yaml`
  (`experiments/checkout_worker.py:26`, `harbor/templates/solve.sh`). Its exact
  bytes are hashed as `submission_sha256` in `sapi-lab-verification/v1`.
- **package** — a self-contained Harbor task directory assembled before a run
  from canonical sources, never maintained by hand. Generation packages contain
  no reference YAML.
- **provenance** — two distinct uses, both long-standing and both clear from
  context: *native execution provenance*, the evidence that the claimed nodes
  really ran (`verification/n8n_provenance.py`), and *source provenance*, the
  pinned upstream specification and the hash manifest of this checkout
  (`provenance/`, `experiments/provenance.py`).
- **run** — one report directory, `reports/<timestamp>/`, holding one suite's
  controls, task packages, jobs and `report.json`. Report directories are
  immutable; a fresh attempt always gets a new directory.
