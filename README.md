# Sapi config lab

A research project: describe a task in YAML → compile it → execute it in real
n8n → independently verify the result. Harbor organizes the entire experiment:
it creates environments, runs solutions and checks, and collects scores.

The baseline is Sapiens commit `06ddd3333109cea8a2cb3071609070d7a3c0d3ff`.
This project implements our limited `sapi-lab/v0` profile, not the entire Haskell
specification. Reorganizing the code does not update the specification.
Show substantial differences before updating the pinned specification.

Project code, comments, documentation, and newly authored reports use English.
Historical evidence, upstream source material, and task input data retain their
original contents. Translating a document included in a model prompt changes
future prompt hashes; saved prompts and previous results are never rewritten.

Start with the guide for your task. Agents changing code read [AGENTS.md](AGENTS.md) first:
it names the compile, execute and evaluate stages and what each may import.

- [Learn the vocabulary first](docs/GLOSSARY.md): *admission*, *case*, *task*, *control*, *candidate* and *reward* each name several different things in this tree. The glossary fixes which one each document means, and states plainly why execution success, acceptance and quality are three separate facts.
- [Add a scenario](docs/AUTHORING.md): the eight files a new task family touches, with the command to run as the gate after each one.
- [Run the complete single-case Checkout Recovery evaluation](docs/CHECKOUT-EVALUATION.md): generated YAML, real n8n, original simulator and rubric. The first public synthetic case scored 9.33/10; this is not a general benchmark performance claim.
- [View workflow graphs and prepare manual runs in local n8n](docs/N8N-UI.md).
- [Understand the supported YAML profile](docs/PROFILE.md) and [author YAML](generation/README.md).
- [Read the recorded evidence and its limits](#evidence-and-limits).
- [Check what is blocked today](#what-does-not-run-right-now) before planning a live run.
- [Verify or compare the pinned specification](docs/SPEC-SOURCE.md).

For the local n8n UI, start with `uv run --locked sapi-lab ui open --all`: it imports
the eight standalone examples as inactive graphs, prints their editor links and
opens n8n without running them. Config 05 is explicitly reported as requiring its
lifecycle controller.
Repeating the command reuses existing links and preserves edits made in n8n;
`--new-copy` creates a fresh copy from YAML.
To prepare one manual live session, use
`uv run --locked sapi-lab ui open benchmarks/09-priority-support-brief/config.yaml --live`, then
press **Execute workflow** in n8n. The [UI guide](docs/N8N-UI.md) covers login,
the first-use wrapper identity, execution limits and result inspection.

## Run

Requirements: **uv 0.12.4 or later, Node.js, and Docker with Compose**. uv selects Python 3.14.8
from `.python-version`. PyYAML is the main Python dependency; Harbor is an
optional extra. Resolved dependency versions and hashes are in `uv.lock`.
The project supports Python 3.14; 3.15 prereleases and older minor versions are
outside the tested range. Host and container use the same Python patch release.
An immutable Astral download catalog in `pyproject.toml` makes the patch pin
available even when uv's bundled catalog predates it.

```bash
uv sync --locked --extra harbor
./run.sh
```

The single command `./run.sh` runs local tests, builds the pinned n8n 2.41.5
image, performs 12 transport probes, and runs three scenarios through Harbor
0.21.0. Each scenario has an oracle trial (reference solution, reward 1) and a
nop trial (no solution, reward 0). Checks cover results, invalid inputs, and a
deliberately corrupted workflow. The report is `reports/<timestamp>/report.json`.
Exit code 0 requires successful checks, not merely an error-free n8n process.
Each case separates engine execution, independent acceptance, fixture inputs,
Agency HTTP calls, and native/workflow-authored evidence. See the
[report field guide](docs/REPORTS.md) before interpreting execution success.

Other commands:

```bash
# Fast local tests; the JS driver used here is NOT real n8n
uv run --locked python -m unittest discover -s tests -v
uv run --locked ruff check src tests verification infra
uv run --locked ruff format --check src tests verification infra
uv run --locked mypy
uv run --locked python infra/check_distribution.py

# Compilation only; no execution
uv run --locked sapi-lab compile benchmarks/01-invoice-total/config.yaml --output /tmp/invoice.n8n.json
uv run --locked sapi-lab build --output-dir /tmp/sapi-generated

# Full control suite, followed by a separate series with live model calls
./run.sh --live

# Model-authored YAML: three independent attempts per task
./run-generation.sh

# Inspect assembled Harbor packages without running them
uv run --locked sapi-lab package-tasks /tmp/sapi-tasks
```

`run.sh` and `run-generation.sh` are thin wrappers around
`uv run --locked --extra harbor sapi-lab harbor` and `sapi-lab generate`.
Use `uv run --locked sapi-lab --help` for the CLI; it lists the commands you run
separately from the entry points a container or another command invokes, and each
command also accepts `--help`. Package commands replace the old Python entry points in the project
root and `scripts/`: see the [migration table](docs/ARCHITECTURE.md).

Live calls require the existing wrapper at `http://127.0.0.1:8765/run`.
The model and authentication are inherited from that wrapper. A separate local
Agency adapter starts on port 18765 and stops after the series. The wrapper is
not modified. Responses are neither automatically repaired nor replaced by stubs.

## What does not run right now

Read this before planning a run. The unpaid paths work; the paid ones do not.

**Live model calls are gated and the gate currently fails.** `sapi-lab live`,
`sapi-lab ui open --live` and the bounded expansion live series all require an
inspected wrapper identity file and check it before dispatching anything. The
recorded file, `evidence/20261004-open-tasks-integration/wrapper-identity.json`,
names two host files with their SHA-256. One of them, the Codex configuration,
no longer matches on this machine, so `wrapper_identity()` refuses with
`Wrapper/config identity changed` and no call is made. The wrapper process
itself is still listening on `127.0.0.1:8765` and still answers the unpaid
readiness probe — it is the recorded identity that is stale, not the port.
Restoring these paths means a fresh read-only inspection of the wrapper and a
new identity record; nothing in this repository can produce one.

`sapi-lab generate` has no such gate and would still reach the wrapper. That is
not a reason to treat it as safe: the configuration that selects the model is
exactly the file whose hash changed, so which model a call would now bill is no
longer established. Inspect the wrapper before dispatching.

**Judged rubric criteria are never scored.** `verify.py` constructs no judge, so
any rubric card with `llm` criteria — today only `support-review-packet` —
writes an `evaluation.json` with status `not_evaluated`, the reason "this card
has judged criteria and no judge is reachable here", and a null score. It is
never a zero and it never fails a submission. The two binary cards
(`invoice-total`, `dual-ledger-closeout`) need no judge and do score.

**What still works.** Compilation, `build`, `package-tasks`, the unit suite, the
lint and type checks, the distribution check, and the complete unpaid control
suite `./run.sh` — which needs Docker but no model call. The separate
AutoWFBench benchmark track has its own judge, invoked against the pinned
upstream source rather than this wrapper, and is not affected by the gate above.

## Project layout

```text
src/sapi_config_lab/
  compile/           YAML definition -> n8n JSON and step map; no I/O, no verdicts
  execute/           run an artifact: n8n executor, Agency adapter, Harbor/Docker helpers
  evaluate/          host-side scoring and reporting of recorded runs (AutoWFBench track)
  author/            model-driven authoring and repair; produces YAML, never compiles it
  coordinate/        cli.py, case selection, budgets, task packaging, the series runners
  *.py, bindings.yaml shared contracts: profile, backend contract, evidence encoding, paths
benchmarks/NN-<scenario>/  one scenario: config.yaml, task.md, instruction.md, cases.json, scenario.json
verification/        the independent verifier: contracts, business checks, rubric cards
harbor/templates/    shared Harbor task templates
generation/          the authoring format description and the AutoWFBench catalogs
tests/               local tests; support/ contains the JS driver
infra/               pinned Docker image containing real n8n
docs/                glossary, authoring guide, profile, architecture, research questions
docs/history/        frozen records of past runs; not current instructions
evidence/            committed extracts of the artefacts the documentation cites
provenance/          pinned public specification and comparison hashes
reports/             where new runs write (ignored, not distributed); empty until one does
var/ui/              local n8n UI state, created by `sapi-lab ui` (ignored)
```

`evidence/` is the only committed run artefact tree. It holds a byte-for-byte
copy of each artefact that `README.md` or `docs/**` links to, at the path it had
under `reports/`, so those links resolve from a clean clone; the run directories
they came from were archived out of the repository and are not reconstructible
from it. [`evidence/README.md`](evidence/README.md) records what was extracted
and what was dropped. `reports/` is where new runs still write, and it is
ignored; it is empty in a fresh checkout.

`generated/` and `validation/` contain local prototype artifacts. Raw reports,
review dumps, environment snapshots, and generated exports stay in the working
checkout and are excluded from Git, Docker build context, and distributions.
The public [results summary](docs/history/RESULTS.md) records selected outcomes and hashes;
reproduction does not require the historical private output directories.

**Complete packages are assembled before each run** from the scenario's
`benchmarks/` directory, the shared templates, and the independent verifier. Exact copies are saved
in `reports/<run>/task-packages/` for reproducibility. Each package contains only
its own scenario's test inputs. There is no manual synchronization step.

## The path of one test

1. Our experiment runner assembles the packages and invokes Harbor.
2. Harbor creates a container with real n8n. Oracle copies reference YAML to
   `/app/submission/config.yaml`; the generation agent writes the model's answer instead.
3. Harbor runs the packaged `tests/test.sh`, in three steps. First the
   independent verifier (`/tests/verify.py plan`) states every definition that
   must run: the submission with each fixture's inputs, the negative inputs, a
   deliberately corrupted artifact and three invalid definitions.
4. The lab runtime (`sapi_config_lab.coordinate.observe`) runs exactly that plan
   through the n8n adapter, which validates YAML, compiles JSON, imports it and
   executes it by ID. It saves `case.json`, the generated graph, the step-to-node
   mapping, logs and raw n8n execution data under `/logs/verifier/evidence/`,
   then `observation.json` with the plan's hash and the hash of every file it
   recorded. It decides nothing.
5. `verify.py evaluate` recomputes the plan, rejects a record that is missing,
   partial, edited, holds extra files, lacks the native artifacts its status
   requires, disagrees with its own native records or is of other inputs, checks the packaged runtime was the one
   that ran, and only then checks the total, selected branch or combined
   sources together with native node evidence. It never runs n8n itself.
6. Each decision is written to `acceptance.json` under the sibling
   `/logs/verifier/evaluation/`, with the summary in `report.json` there; the
   evidence directory is never written again. `test.sh`
   writes reward 1/0 to `/logs/verifier/reward.txt`. Harbor collects these
   files; the experiment runner copies them to `reports/<run>/jobs/` and writes
   the overall `report.json`.

Separate Python code in the verifier computes expected totals from input invoices.
Routing checks cover the threshold and selected branch; research checks cover
sources, quotations, and data merging. These automatic gates use no LLM-as-a-judge;
qualitative agent review is reported separately.

Each case gets a separate SQLite database. Existing user workflows and credentials
are neither imported nor modified. Test graphs do not appear at localhost:5678:
they run in separate temporary CLI environments that are removed afterwards.

## Evidence and limits

The seven static scenario families have completed the path **natural-language task →
model-authored YAML → compiled native n8n graph → accepted execution**, with real
model calls for their LLM operations. Refinement (04) and lifecycle (05) now have
separate implementations and evaluations below. Implementation support does not
mean every handwritten reference file was replayed with live models. The model
receives the bounded profile and operation catalog; these results do not establish
arbitrary workflow generation.

| Config | Exact reference config: recorded native n8n coverage | Model-authored YAML: recorded native live coverage |
| --- | --- | --- |
| [01-invoice-total](benchmarks/01-invoice-total/config.yaml) | Positive/negative controls; live-mode reference cases passed | [3 cases passed; 0 runtime model calls](evidence/20261004-generated-live-integration/SUMMARY.md) — Script-only |
| [02-ticket-routing](benchmarks/02-ticket-routing/config.yaml) | Positive/negative controls; reference live classification passed | [2 cases passed; 2 runtime model calls](evidence/20261004-generated-live-integration/SUMMARY.md) |
| [03-competitor-report](benchmarks/03-competitor-report/config.yaml) | Positive/negative controls; reference live research passed | [2 cases passed; 6 runtime model calls](evidence/20261004-generated-live-integration/SUMMARY.md) |
| [04-revise-answer](benchmarks/04-revise-answer/config.yaml) | One bounded native graph; stub controls show rejection, feedback, later acceptance and exhaustion | [3 one-shot YAMLs accepted; selected original used 4 runtime calls](evidence/20261004-open-tasks-integration/refinement-evaluation-2/SUMMARY.md): positive accepted first draft; impossible-limit case exhausted 3 attempts with no accepted output |
| [05-daily-digest](benchmarks/05-daily-digest/config.yaml) | Candidate execution and an explicit durable controller; native controls use an injected clock | 1 one-shot authoring, 5 runtime and 1 WBS repair call. [Unchanged authored revision passed Callback and actual-clock Cron](evidence/20261004-open-tasks-integration/lifecycle-evaluation-3/after-authored/authored/report.json); [separate deliberate mutation was rejected, then model repair passed a new Callback and actual-clock Cron](evidence/20261004-open-tasks-integration/lifecycle-evaluation-3/after-mutation/mutation/report.json) |
| [06-dual-ledger-closeout](benchmarks/06-dual-ledger-closeout/config.yaml) | Positive/negative native controls; Script-only | [2 cases passed; 0 runtime model calls](evidence/20261004-scenario-expansion-integration/sc01-live/report.json) — Script-only |
| [07-support-review-packet](benchmarks/07-support-review-packet/config.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 cases passed; 4 runtime model calls](evidence/20261004-scenario-expansion-integration/sc02-live/report.json) |
| [08-bulletin-market-brief](benchmarks/08-bulletin-market-brief/config.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 corrected-task cases passed; 8 runtime model calls](evidence/20261004-corrected-expansion-integration/sc03-live/report.json) |
| [09-priority-support-brief](benchmarks/09-priority-support-brief/config.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 corrected-task cases passed; 5 runtime model calls](evidence/20261004-corrected-expansion-integration/sc04-live/report.json) |

Runtime call counts exclude YAML-authoring and WBS repair calls. Zero calls for invoice-only
workflows are expected, not missing LLM coverage. Negative controls pass when
invalid inputs or deliberately corrupted graphs are correctly rejected.
Reference coverage is tied to saved config hashes; each execution records its
fixture-input and, for live execution, deadline overlays.
The 05 actual-clock runs use recorded fixed-minute evaluation schedule overlays;
they do not install a persistent scheduling service.

Reference evidence: [01–03 controls](evidence/20261004-scenario-expansion-integration/baseline-control/report.json)
and [01–03 live](evidence/20261004-verified-2/live/report.json),
[06 controls](evidence/20261004-scenario-expansion-integration/sc01-generation/control/report.json),
[07 controls](evidence/20261004-scenario-expansion-integration/sc02-generation/control/report.json),
[08 controls](evidence/20261004-corrected-expansion-integration/sc03-generation/control/report.json),
and [09 controls](evidence/20261004-corrected-expansion-integration/sc04-generation/control/report.json).
These links target the committed `evidence/` tree, which holds exactly the
artefacts cited here and nothing else. The historical generated-live report's
container-preservation failure is preserved and explained by the user-confirmed
service stop in its summary; all seven workflow cases passed their business gates.
The initial SC-03 rejection also remains preserved; the later corrected-task
series is separate evidence. [Independent agent content review](evidence/20261004-open-tasks-integration/AGENT-QUALITY-REVIEW.md)
found the SC-03/04 briefs concise and supported by these short fixtures. SC-02
correctly reports its overlong support drafts as rejected; packet acceptance does
not make those drafts ready to send. Human review is separate and is not claimed.
The earlier third-authoring gate is a proposed team-pilot policy, not a specification
or statistical rule. These bounded results do not establish team-pilot readiness.

The [04 evidence review](evidence/20261004-open-tasks-integration/INDEPENDENT-REVIEW.md)
also assessed its accepted reply as cautious and useful. Live rejection followed
by acceptance was not observed: that path was established with native stub controls.
The [lifecycle guide](docs/LIFECYCLE.md) explains why 05 needs a controller outside
the imported candidate graph. Its [finite evaluation](docs/LIFECYCLE-EVALUATION.md)
separates original authoring from a deliberate mutation and model-authored repair.

Two [composition experiments](docs/GENERALIZATION-EVALUATION.md) completed four
unchanged one-shot authorings and four native live cases with 12 runtime calls:
[billing plus a digest](evidence/20261004-open-tasks-integration/generalization-evaluation/series/billing-bulletin-packet/live/report.json)
and [two audience briefs](evidence/20261004-open-tasks-integration/generalization-evaluation/series/two-audience-briefs/live/report.json).
Both answers per task used the same graph shape. For the briefs, the models shared
one product analysis and supplied writer inputs as inline objects; the two manual
native controls instead used shared analysis with combine steps or separate product
analyses. These specific alternatives passed; structural diversity between model
attempts was not observed. The models selected operations, bindings, reuse and
output structure within the existing catalog. Automatic prose checks are lexical;
the independent agent read found the short outputs faithful to their supplied facts.
This does not establish new business implementations or arbitrary workflow generation.
See the [runtime limitations](docs/PROFILE.md#research-harness-transport-modes).

The [local UI demonstration](evidence/20261004-open-tasks-integration/ui-demo/run/verification.json)
manually executed a new inactive workflow (`a306069acac34f50`, execution `61`)
with four correlated runtime model calls. The six pre-existing workflow hashes
were preserved. Its foreground helper is stopped; use the [UI guide](docs/N8N-UI.md)
to prepare a new bounded run.

Compilation and execution interfaces make another backend easier to add, but only
n8n is implemented. The Sapiens harness and computer use are not connected.
Provenance checks also depend on n8n; another
executor will need its own evidence checks.

These are historical outcomes, not claims that live calls were repeated for the
current sources. See [curated results and evidence hashes](docs/history/RESULTS.md).
Every new control run saves source hashes, host/runtime versions, and verifies
that public sources stayed unchanged during execution.

CI runs Ruff, mypy, local behavioral tests, and distribution checks. The separate
Docker/Harbor job runs only when selected in a manual workflow dispatch. It uses
stubbed model operations and never enables live calls or uploads raw artifacts.

## Reading order

There are more documents here than anyone needs. Read the first four in order;
reach for the rest only when the task calls for them.

**Start here, in this order:**

| Document | Who it is for |
| --- | --- |
| [Glossary](docs/GLOSSARY.md) | Everyone, first. Fixes one meaning per overloaded word and states the three facts the rest of the documentation assumes |
| [Architecture and commands](docs/ARCHITECTURE.md) | Everyone. The three module groups, the full command table split into commands you run and container entry points, and the command migration map |
| [Report field guide](docs/REPORTS.md) | Anyone about to read a run's output, before concluding anything from it |
| [Adding a scenario](docs/AUTHORING.md) | Anyone adding or changing a task family. The eight files involved, and the command that checks each one |

**When the task calls for it:**

| Document | Who it is for |
| --- | --- |
| [Profile](docs/PROFILE.md) and [`generation/FORMAT.md`](generation/FORMAT.md) | Writing or reviewing `sapi-lab/v0` YAML |
| [`verification/README.md`](verification/README.md) | What acceptance checks, and how the rubric layer sits beside it |
| [`generation/README.md`](generation/README.md) | The model-authoring track and what that pilot does and does not establish |
| [Local n8n UI](docs/N8N-UI.md) | Viewing graphs and preparing one bounded manual run |
| [Expansion run guide](docs/SCENARIO-EXPANSION.md) | Running the ordered, budgeted four-scenario series |
| [Lifecycle](docs/LIFECYCLE.md) | Config 05 and why it needs a controller outside the graph |
| [Frozen generated replay](docs/GENERATED-LIVE.md) | The `live --submissions-manifest` command and its unpaid gate |
| [Specification source](docs/SPEC-SOURCE.md) | Verifying or comparing the pinned Sapiens revision |
| [AutoWFBench environment](docs/AUTOWFBENCH-ENVIRONMENT.md) | The separate benchmark track's pinned task interface |

**Records of finished work, not instructions.** These describe what a particular
experiment established and when. They are accurate about their own run and say
nothing about the current tree:
[extension acceptance](docs/EXTENSION-ACCEPTANCE.md),
[lifecycle evaluation](docs/LIFECYCLE-EVALUATION.md),
[generalization evaluation](docs/GENERALIZATION-EVALUATION.md),
[checkout evaluation](docs/CHECKOUT-EVALUATION.md),
[two-task business evaluation](docs/BUSINESS-EVALUATION.md), and
[research questions](docs/RESEARCH-QUESTIONS.md), which is dated October 4, 2026.
The runners for the composition and lifecycle series were retired after those
experiments finished; [docs/RETIRED.md](docs/RETIRED.md) names the revision that
reruns them.

**Frozen.** Dated records of individual runs, including the
[original analysis](docs/history/ANALYSIS.md) and the
[curated results](docs/history/RESULTS.md), are frozen under
[docs/history/](docs/history/README.md). They are byte-exact by policy, are
never updated, and are not current instructions — do not read a status line
there as a thing to do now. Some of their relative links point at files that
have since moved; that rot is deliberate and is not repaired.
Historical reports retain their old paths; see the
[file migration map](docs/MIGRATION-PATHS.json), which records the 2026-10-04
refactor and is not updated afterwards: its `ANALYSIS.md` entry now resolves to
`docs/history/ANALYSIS.md`.
