# Adding a scenario

A *scenario* is one named task family: a reference YAML in [`configs/`](../configs/),
a natural-language task, a set of test inputs, and an independent statement of
what a correct answer must do. Taking one all the way through means editing
**eight files in five directories**: six are needed for any scenario at all, a
seventh for the model-authoring track, an eighth if it joins the bounded live
series. Nothing discovers a scenario automatically, and each omission fails at a
different point with a different message. This guide walks one concrete new
scenario from an empty file to a passing control run, and says what success
looks like at each gate.

Terms used here — *scenario*, *case*, *oracle*, *nop*, *acceptance*, *occurrence* —
are fixed in the [glossary](GLOSSARY.md). Read the three framing facts it opens
with before you start; two of them are restated below because they are what
mislead people here.

## The places a new scenario touches

| # | File | What you add | What breaks if you skip it |
| --- | --- | --- | --- |
| 1 | `configs/NN-<scenario>.yaml` | The reference workflow | Nothing to compile; package staging fails to copy `environment/base.yaml` |
| 2 | `src/sapi_config_lab/core/scenarios.py` | `"<scenario>": "NN-<scenario>.yaml"` in one group dict | `ValueError: Unknown, duplicate, or empty scenario selection`. The name is not offered by `sapi-lab harbor --scenario` or `verify.py --scenario` |
| 3 | `harbor/tasks/<scenario>/instruction.md` | The container-facing instruction | `FileNotFoundError: .../harbor/tasks/<scenario>/instruction.md` during `package-tasks` |
| 4 | `verification/cases.json` | `positive`, `negative`, and `live_cases` | `KeyError: '<scenario>'` in `stage_tasks`, and again in `verify.py` when it loads its fixtures |
| 5 | `verification/scenario_contracts.py` | A `CONTRACTS` entry: roles, edges, output | `Rejected: Unknown acceptance scenario` from `contract_for()`, which both role binding and the native ordering check call |
| 6 | `verification/scenario_business.py` | A branch in `check_scenario_business_result` | `Rejected: Unknown expansion acceptance scenario` — the catch-all at the end of the dispatch |
| 7 | `generation/tasks.json` | The public task text | `KeyError: '<scenario>'` in `package-tasks --mode generation`. Only the model-authoring track needs this |
| 8 | `src/sapi_config_lab/interfaces/expansion.py` | A `RUNTIME_CAPS` entry: case name → model-call ceiling | `KeyError: '<scenario>'` when an `ExpansionSeries` is constructed, and in `case_budget()`. Only needed if you registered in `EXPANSION_SCENARIOS` |

Two more places, outside the eight:

- `verification/rubric_cards.py` — genuinely optional. Without a card the
  scenario simply writes no `evaluation.json`; `card_for()` raises `RubricError`
  and `evaluate()` returns `None`, which is handled. See
  [Rubric, optional](#rubric-optional).
- Three unit tests hard-code the current size of the scenario set and will fail
  until someone updates them — not optional, just not a registration:
  `tests/test_ui.py` (the `--all` row count),
  `tests/test_occurrence_budget.py` and `tests/test_expansion_runner.py` (the
  frozen authoring and runtime ceilings). These numbers are deliberate: the
  series ledger freezes a budget, so a changed budget must be a visible edit.

`verification/*.py` is copied into each task package by glob, so a *new verifier
module* needs no registration anywhere. `verification/cases.json` is sliced per
scenario at packaging time, so a package only ever carries its own fixtures.

## Before anything: the catalog is closed

A step may only use an operation registered in
[`src/sapi_config_lab/core/bindings.yaml`](../src/sapi_config_lab/core/bindings.yaml).
The validator rejects anything else before compilation:

```text
Invalid workflow.steps[0].uses: Unknown operation: invoices.deduplicate
```

Design your scenario out of what is already there. The current catalog is
`invoices.validate|sum|report`, `ticket.classify|escalation_draft|normal_draft`,
`branch.select_one`, `research.product|marketing|combine|write`,
`reply.generate|check`, and `digest.prepare|summarize|preview`. The two
AutoWFBench tasks have their own separate catalogs
(`generation/checkout-bindings.yaml`, `generation/crm-bindings.yaml`); they are
not available to lab scenarios.

**If a new operation is genuinely needed**, it is a larger change than a
scenario and does not belong in the same commit. It needs all of:

1. A catalog entry in `core/bindings.yaml`: `kind` (`Script` or `LLM`), declared
   `inputs` and `outputs`, and `implementation` (`local_js` or `agency_bridge`).
   An `LLM` operation also needs `prompt`, `output_contract`, `input_schema` and
   `output_schema` — the schemas are what the live transport enforces on the
   wrapper's answer.
2. A deterministic implementation in
   `src/sapi_config_lab/runtime/n8n/operations.js`. Stub and live transports are
   held to the same declared output contract, so the stub is not a toy: it is
   the reference behaviour the live path is checked against.
3. An independent re-statement in `verification/` of whatever the operation now
   lets a scenario claim. The business checker must not import the operation.

Adding an operation changes the prompt every authoring task sees, so it also
changes future prompt hashes. Do not fold it into a scenario commit.

## The worked example

The scenario below is deliberately plain: `tri-ledger-closeout`, a third ledger
added to the shape of [`06-dual-ledger-closeout.yaml`](../configs/06-dual-ledger-closeout.yaml).
It is Script-only, so every gate up to the Harbor run can be checked without
Docker and without a model call. [LLM scenarios](#if-your-scenario-uses-an-llm-operation)
need extra work, listed at the end.

### Step 1 — the reference config

Write `configs/10-tri-ledger-closeout.yaml`. The field semantics are in
[the profile](PROFILE.md#field-semantics) and the authoring rules a model is
given are in [`generation/FORMAT.md`](../generation/FORMAT.md). `workflow.id`
**must equal the scenario name**: `bind_roles()` rejects a submission whose
`workflow.id` differs ("Wrong submitted workflow identity").

Number the file after the current highest. Numbering matters: `tests/test_workflows.py`
addresses configs positionally by sorted filename, so appending is safe and
inserting in the middle silently re-points existing tests.

Gate:

```bash
uv run --locked sapi-lab compile configs/10-tri-ledger-closeout.yaml --output /tmp/tri.n8n.json
```

Success looks like one JSON line and two files written:

```json
{"compiled": true, "executed": false, "artifact": "/tmp/tri.n8n.json", "llm_mode": "stub"}
```

`compile` knows nothing about scenarios — it validates and compiles any config,
registered or not. A green `compile` means the profile and the catalog accept
your YAML, and nothing more. `uv run --locked sapi-lab build --output-dir DIR`
does the same for every config at once and records in `build-results.json` which
were rejected. Config 05 is expected to appear there with
`"valid_profile": true` and `"demo_export": "rejected"`: its YAML is valid, and
the demo compiler declines to export it because it needs a persistent
orchestrator. That is a correct result, not a failure to fix.

### Step 2 — register the scenario

In `src/sapi_config_lab/core/scenarios.py`, add the name to **one** group:

- `BASELINE_SCENARIOS` is the default selection — the three tasks `./run.sh`
  runs when you pass no `--scenario`. Do not add to it casually; every default
  control run gets longer.
- `EXPANSION_SCENARIOS` is the bounded, opt-in composition series described in
  [the expansion guide](SCENARIO-EXPANSION.md). Registering here is what makes
  a `RUNTIME_CAPS` entry (step 8) mandatory, and it joins the frozen series
  budget.
- `EXTENSION_SCENARIOS` and `LIFECYCLE_SCENARIOS` hold the two scenarios with
  their own execution semantics (bounded refinement, durable lifecycle). A new
  scenario almost certainly does not belong there.

`SCENARIOS` is the union, and it is what `--scenario` on `sapi-lab harbor` and
`verify.py` offers.

Gate:

```bash
uv run --locked sapi-lab harbor --help
```

Your name appears in the `--scenario` choice list. That is the whole check.

### Step 3 — the container instruction

Create `harbor/tasks/tri-ledger-closeout/instruction.md`. This is the text the
oracle and live packages carry. Copy the surrounding wording from an existing
one — the header that names `/app/submission/config.yaml`, and the footer that
points at the profile and catalog *inside the image* and forbids touching
anything but the submission.

The middle paragraph is the task itself, and it is normally the same sentence
you will put in `generation/tasks.json` (step 7). That duplication is real and
nothing enforces it; keep the two in step by hand.

### Step 4 — the test inputs

Add a `"tri-ledger-closeout"` key to `verification/cases.json` with three parts:

- `positive` — a list of `{name, inputs}`. The verifier deep-copies the
  submitted config and replaces `workflow.inputs` with each one. The reference
  config's own inputs are only a sample; they are never what is graded.
- `negative` — `{name, inputs, error, operation, role}`. These must fail
  **inside n8n**, in the named operation. `check_rejection()` requires the run
  to end in `error`, no `Result` node to be present, the error text to contain
  your `error` string, and the node that `role` binds to to be the one that
  carries the failure. `role` is a role name from your contract (step 5), not a
  step ID. Add `schema_error` when a live-mode schema message differs.
- `live_cases` — the subset of positive case names that live mode may run.
  Omit it and live mode runs every positive case.

For scenarios whose output is prose, positive cases also carry
`product_anchors` / `marketing_anchors` / `forbidden_anchors` — the lexical fact
contract. See [If your scenario uses an LLM operation](#if-your-scenario-uses-an-llm-operation).

Gate:

```bash
uv run --locked sapi-lab package-tasks /tmp/tri-oracle --scenario tri-ledger-closeout
```

It prints the destination and exits 0. The destination must not already exist.
Success looks like `tri-ledger-closeout/` holding `instruction.md`, `task.toml`,
`environment/{Dockerfile,base.yaml}`, `solution/solve.sh` and a `tests/`
directory with every `verification/*.py` plus a `cases.json` containing **only
your scenario**. If `tests/cases.json` holds another scenario's fixtures,
something is wrong with the slice, not with your data.

### Step 5 — the independent contract

Add an entry to `CONTRACTS` in `verification/scenario_contracts.py`:

```python
"tri-ledger-closeout": {
    "roles": {...},    # role name -> operation, kind, and where each input must come from
    "edges": [...],    # (earlier_role, later_role) pairs
    "output": {...},   # the final output shape, written in refs to roles
},
```

This is the project's statement of the obligation, written without reference to
the submitted YAML. It binds **roles**, not step IDs: a submission may name its
steps anything and list them in any order, and `bind_roles()` finds the
occurrence that matches each role by operation, input origin, guard and lineage.
It rejects missing, extra, swapped and ambiguous bindings.

`edges` is used twice: once for lineage and once by
`check_operation_order()`, which requires each earlier node to have *finished*,
by native n8n start and execution times, before the later one started.

### Step 6 — the independent business check

Add a branch to `check_scenario_business_result` in
`verification/scenario_business.py`. The branch receives the fixture `inputs` and
a `WorkflowObservation`, and must recompute the expected answer from the inputs —
in plain Python, importing no compiler and no operation implementation. The
helpers already there cover the common shapes: the module-level `_ledger`,
`_routing` and `_reply_review`, and the `facts` and `fact_contract` closures
defined inside the dispatch.

Two habits worth copying from the existing branches:

- Also assert what must **not** be there. The dual-ledger branch checks that no
  ledger's step state contains another ledger's step IDs, which is how it proves
  the ledgers were independent rather than merely correct.
- Raise through `require`/`equal` from `verification/contracts.py`. A `Rejected`
  is an honest rejection; any other exception is a verifier bug.

Gate — run the unit suite:

```bash
uv run --locked python -m unittest discover -s tests -v
```

On a clean tree every test passes (330 tests, 23 skipped, at the time of
writing). After adding a scenario, expect exactly the three counting tests named
above to fail, and nothing else. Anything else failing is your scenario, not the
budget.

### Step 7 — the natural-language task

Add the task text to `generation/tasks.json`, keyed by scenario name. This is
the prompt a model is given with no reference YAML: it must name the logical id,
the input field names, the exact output shape, the rules that must hold, and a
sample input. It must not leak the reference step IDs — the contract binds
roles, so the model is free to choose its own.

Gate:

```bash
uv run --locked sapi-lab package-tasks /tmp/tri-generation --mode generation --scenario tri-ledger-closeout
```

Success is a package whose `instruction.md` is your task text followed by
`FORMAT.md`, `docs/PROFILE.md` and the full catalog, and which contains **no**
`solution/` and **no** `environment/base.yaml`. Its Dockerfile removes
`/app/lab/configs` and `/app/scenario` inside the container. If a reference
config appears in a generation package, stop: `tests/test_packaging.py` exists
to catch exactly that.

### Step 8 — the series budget, if you joined the expansion series

If you registered in `EXPANSION_SCENARIOS`, add a `RUNTIME_CAPS` entry in
`src/sapi_config_lab/interfaces/expansion.py` mapping each *live* case name to
the number of model calls that case may make. Script-only cases take `0`; a cap
of `0` is a real cap, not "unmeasured". The series ledger derives its frozen
ceiling by summing these, which is why the two ceiling tests fail until they are
updated.

### Step 9 — the real gate

Everything above runs without Docker. The actual gate is a control run:

```bash
./run.sh --scenario tri-ledger-closeout
```

This builds the pinned n8n 2.41.5 image, runs the 12 transport probes, then runs
your scenario twice in the same run: once as `oracle` and once as `nop`. It
writes `reports/<timestamp>/report.json` and exits 0 only if every check passed.

Success looks like: the oracle trial scoring reward 1 **and** recording
`acceptance.passed: true`, the nop trial scoring reward 0, both with no
exception, and the corruption probes reporting that each deliberately broken
result was rejected.

## oracle and nop are one run, not two kinds of run

The commonest misreading. `oracle` and `nop` are two Harbor *agents* over the
same task set in the same invocation. The oracle agent copies the reference YAML
to `/app/submission/config.yaml`; the nop agent writes no submission at all. The
run asserts both outcomes in the same block: oracle must score 1, nop must score 0.

Neither is "the real run". They are the positive and negative controls of your
measuring instrument:

- **Oracle did not score 1** → the instrument is broken. The image, the
  transport, the packaging or the verifier is wrong. It says nothing about your
  scenario's difficulty.
- **Nop did not score 0** → the verifier accepts a submission that does not
  exist. Your contract or your business check has a hole big enough to pass
  vacuously, and that is the one failure you must never work around.

Neither outcome is a reason to rerun and hope. `sapi-lab generate` will not
dispatch a single paid authoring call while the control suite fails.

## Engine success, acceptance and rubric quality are three facts

Your scenario produces three independent verdicts. Keeping them apart is the
whole point of the verifier's structure.

| Fact | Who decides | Where it is recorded |
| --- | --- | --- |
| **Engine success** | The runtime: n8n imported the graph, executed it, and a `Result` node produced data | `execution.succeeded` in `sapi-lab-execution/v1` |
| **Acceptance** | The independent verifier: business criteria **and** native provenance both held | `acceptance.passed`, and the verifier's `report.json`; it is what writes `reward.txt` |
| **Rubric quality** | The rubric layer, scoring a weighted card — if the scenario has one, and only as far as it can | `evaluation.json`, schema `sapi-lab-rubric-evaluation/v1` |

They do not imply each other in either direction. A deliberately corrupted
workflow can have `execution.succeeded: true` and rejected acceptance — and the
negative test then *passes*, because rejection was expected. An accepted packet
can contain a draft a support agent would not send; acceptance never claimed
otherwise.

The rubric never touches the reward. `harbor/templates/test.sh` writes `0`,
runs `verify.py`, and writes `1` only on exit code 0. `evaluation.json` is
written beside `report.json` and read by nobody in that chain. `evaluate()` is
written not to raise, and `verify.py` wraps it anyway, because the one thing a
rubric must never do is turn an accepted submission into a failure.

## Rubric, optional

Add a `RubricCard` to `verification/rubric_cards.py` and register it in `CARDS`
only when the scenario has a quality question worth asking. Without a card
nothing is written and nothing fails.

A card is a list of weighted criteria with one of two evaluators, plus a
shortcut for the degenerate case:

- `RubricCard.binary(name)` is the shortcut — a single ten-point criterion that
  mirrors acceptance, which is what `invoice-total` and `dual-ledger-closeout`
  use.
- A `deterministic` criterion names a `check_id` that must correspond to an
  obligation `scenario_business.py` already states. The rule is referenced,
  never restated, so a criterion cannot drift from the acceptance it describes;
  `support_review_obligations()` is the pattern to copy.
- An `llm` criterion carries a question and `yes`/`maybe`/`no` anchors, and
  declares which evidence a judge may see.

**A judged card is not scored in the container.** `verify.py` constructs no
judge, so any card with `llm` criteria returns status `not_evaluated` with the
reason "this card has judged criteria and no judge is reachable here" and a null
score — never a zero. Today that applies to `support-review-packet`, the only
judged card. Treat an `llm` criterion as documentation of the question until a
judge is wired in.

## If your scenario uses an LLM operation

Everything above still applies, plus:

- **Fact anchors.** Each positive case needs `product_anchors` /
  `marketing_anchors`: a list of *groups*, each group a set of alternative
  regexes. At least one pattern in every group must match the generated prose,
  so one group states one required fact and its acceptable phrasings. Add
  `forbidden_anchors` for facts the fixture deliberately withholds; none of
  those may match. These are lexical coverage checks. They catch an omitted
  facet and an unrelated report. They do not establish factual or semantic
  quality, and no document should say they do.
- **Stub equality.** In `mode == "stub"` the branch should assert the exact
  deterministic text `operations.js` produces. That is what makes the stub
  control meaningful rather than decorative.
- **Occurrence budget.** Every model call a live case may make must be admitted
  up front in `RUNTIME_CAPS`. An invocation that is not on the list is refused
  by `runtime/agency.py`, and an under-count fails the run rather than silently
  spending more.
- **Live replay is blocked today.** See
  [what does not run right now](../README.md#what-does-not-run-right-now).
  Everything through step 9's stub control suite works. `sapi-lab live`,
  `sapi-lab ui open --live` and the expansion live series all check an inspected
  wrapper identity record first, and that record no longer matches this machine,
  so they refuse before dispatching.

## Where to look next

- [Glossary](GLOSSARY.md) — the vocabulary, and the three framing facts above in
  their authoritative form.
- [Profile](PROFILE.md) — YAML field semantics and validator limits.
- [`generation/FORMAT.md`](../generation/FORMAT.md) — the authoring rules a model
  is given; your reference config should obey them too.
- [`verification/README.md`](../verification/README.md) — what acceptance checks,
  and how the rubric layer sits beside it.
- [Report field guide](REPORTS.md) — how to read what a run wrote.
- [Expansion guide](SCENARIO-EXPANSION.md) — the ordered, budgeted series the
  `EXPANSION_SCENARIOS` group belongs to.
