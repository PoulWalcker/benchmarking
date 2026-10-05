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

Start with the guide for your task:

- [Learn the vocabulary first](docs/GLOSSARY.md): *admission*, *case*, *task*, *control*, *candidate* and *reward* each name several different things in this tree. The glossary fixes which one each document means, and states plainly why execution success, acceptance and quality are three separate facts.
- [Run the complete single-case Checkout Recovery evaluation](docs/CHECKOUT-EVALUATION.md): generated YAML, real n8n, original simulator and rubric. The first public synthetic case scored 9.33/10; this is not a general benchmark performance claim.
- [View workflow graphs and prepare manual runs in local n8n](docs/N8N-UI.md).
- [Understand the supported YAML profile](docs/PROFILE.md) and [author YAML](generation/README.md).
- [Read the recorded evidence and its limits](#evidence-and-limits).
- [Verify or compare the pinned specification](docs/SPEC-SOURCE.md).

For the local n8n UI, start with `uv run --locked sapi-lab ui open --all`: it imports
the eight standalone examples as inactive graphs, prints their editor links and
opens n8n without running them. Config 05 is explicitly reported as requiring its
lifecycle controller.
Repeating the command reuses existing links and preserves edits made in n8n;
`--new-copy` creates a fresh copy from YAML.
To prepare one manual live session, use
`uv run --locked sapi-lab ui open configs/09-priority-support-brief.yaml --live`, then
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
uv run --locked sapi-lab compile configs/01-invoice-total.yaml --output /tmp/invoice.n8n.json
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

## Project layout

```text
src/sapi_config_lab/
  workflow/          YAML format, validation, bindings.yaml
  runtime/           n8n compiler, executor, and Agency adapter
  experiments/       Harbor, YAML generation, task packaging
  cli.py             shared command entry point
configs/             nine examples; 04 adds bounded refinement, 05 needs a lifecycle controller
verification/        independent checks and cases.json test inputs
harbor/              scenario instructions and shared task templates
generation/          model tasks and format description, without reference YAML
tests/              local tests; support/ contains the JS driver
infra/               pinned Docker image containing real n8n
docs/                glossary, profile, architecture, and research questions
docs/history/        frozen records of past runs; not current instructions
provenance/          pinned public specification and comparison hashes
reports/             local experiment evidence (ignored, not distributed)
```

`generated/` and `validation/` contain local prototype artifacts. Raw reports,
review dumps, environment snapshots, and generated exports stay in the working
checkout and are excluded from Git, Docker build context, and distributions.
The public [results summary](docs/history/RESULTS.md) records selected outcomes and hashes;
reproduction does not require the historical private output directories.

Each source directory `harbor/tasks/<scenario>/` now contains only an instruction.
**Complete packages are assembled before each run** from shared templates,
the corresponding config, and the independent verifier. Exact copies are saved
in `reports/<run>/task-packages/` for reproducibility. Each package contains only
its own scenario's test inputs. There is no manual synchronization step.

## The path of one test

1. Our experiment runner assembles the packages and invokes Harbor.
2. Harbor creates a container with real n8n. Oracle copies reference YAML to
   `/app/submission/config.yaml`; the generation agent writes the model's answer instead.
3. Harbor runs the packaged `tests/test.sh`. It invokes the independent
   `verification/verify.py`, mounted as `/tests/verify.py`.
4. The verifier substitutes inputs from its `cases.json` and calls the n8n adapter.
   The adapter validates YAML, compiles JSON, imports it, and executes it by ID.
5. The adapter saves `case.json`, the generated graph, the step-to-node mapping,
   logs, and raw n8n execution data. The verifier independently checks the total,
   selected branch, or combined sources, together with node execution evidence.
6. The verifier writes `/logs/verifier/report.json`. `test.sh` writes reward 1/0
   to `/logs/verifier/reward.txt`. Harbor collects these files; the experiment
   runner copies them to `reports/<run>/jobs/` and writes the overall `report.json`.

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
| [01-invoice-total.yaml](configs/01-invoice-total.yaml) | Positive/negative controls; live-mode reference cases passed | [3 cases passed; 0 runtime model calls](reports/20261004-generated-live-integration/SUMMARY.md) — Script-only |
| [02-ticket-routing.yaml](configs/02-ticket-routing.yaml) | Positive/negative controls; reference live classification passed | [2 cases passed; 2 runtime model calls](reports/20261004-generated-live-integration/SUMMARY.md) |
| [03-competitor-report.yaml](configs/03-competitor-report.yaml) | Positive/negative controls; reference live research passed | [2 cases passed; 6 runtime model calls](reports/20261004-generated-live-integration/SUMMARY.md) |
| [04-revise-answer.yaml](configs/04-revise-answer.yaml) | One bounded native graph; stub controls show rejection, feedback, later acceptance and exhaustion | [3 one-shot YAMLs accepted; selected original used 4 runtime calls](reports/20261004-open-tasks-integration/refinement-evaluation-2/SUMMARY.md): positive accepted first draft; impossible-limit case exhausted 3 attempts with no accepted output |
| [05-digest-lifecycle.yaml](configs/05-digest-lifecycle.yaml) | Candidate execution and an explicit durable controller; native controls use an injected clock | 1 one-shot authoring, 5 runtime and 1 WBS repair call. [Unchanged authored revision passed Callback and actual-clock Cron](reports/20261004-open-tasks-integration/lifecycle-evaluation-3/after-authored/authored/report.json); [separate deliberate mutation was rejected, then model repair passed a new Callback and actual-clock Cron](reports/20261004-open-tasks-integration/lifecycle-evaluation-3/after-mutation/mutation/report.json) |
| [06-dual-ledger-closeout.yaml](configs/06-dual-ledger-closeout.yaml) | Positive/negative native controls; Script-only | [2 cases passed; 0 runtime model calls](reports/20261004-scenario-expansion-integration/sc01-live/report.json) — Script-only |
| [07-support-review-packet.yaml](configs/07-support-review-packet.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 cases passed; 4 runtime model calls](reports/20261004-scenario-expansion-integration/sc02-live/report.json) |
| [08-bulletin-market-brief.yaml](configs/08-bulletin-market-brief.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 corrected-task cases passed; 8 runtime model calls](reports/20261004-corrected-expansion-integration/sc03-live/report.json) |
| [09-priority-support-brief.yaml](configs/09-priority-support-brief.yaml) | Positive/negative native controls with stub LLM; exact reference not separately live-replayed | [2 corrected-task cases passed; 5 runtime model calls](reports/20261004-corrected-expansion-integration/sc04-live/report.json) |

Runtime call counts exclude YAML-authoring and WBS repair calls. Zero calls for invoice-only
workflows are expected, not missing LLM coverage. Negative controls pass when
invalid inputs or deliberately corrupted graphs are correctly rejected.
Reference coverage is tied to saved config hashes; each execution records its
fixture-input and, for live execution, deadline overlays.
The 05 actual-clock runs use recorded fixed-minute evaluation schedule overlays;
they do not install a persistent scheduling service.

Reference evidence: [01–03 controls](reports/20261004-scenario-expansion-integration/baseline-control/report.json)
and [01–03 live](reports/20261004-verified-2/live/report.json),
[06 controls](reports/20261004-scenario-expansion-integration/sc01-generation/control/report.json),
[07 controls](reports/20261004-scenario-expansion-integration/sc02-generation/control/report.json),
[08 controls](reports/20261004-corrected-expansion-integration/sc03-generation/control/report.json),
and [09 controls](reports/20261004-corrected-expansion-integration/sc04-generation/control/report.json).
These links target local ignored reports. The historical generated-live report's
container-preservation failure is preserved and explained by the user-confirmed
service stop in its summary; all seven workflow cases passed their business gates.
The initial SC-03 rejection also remains preserved; the later corrected-task
series is separate evidence. [Independent agent content review](reports/20261004-open-tasks-integration/AGENT-QUALITY-REVIEW.md)
found the SC-03/04 briefs concise and supported by these short fixtures. SC-02
correctly reports its overlong support drafts as rejected; packet acceptance does
not make those drafts ready to send. Human review is separate and is not claimed.
The earlier third-authoring gate is a proposed team-pilot policy, not a specification
or statistical rule. These bounded results do not establish team-pilot readiness.

The [04 evidence review](reports/20261004-open-tasks-integration/INDEPENDENT-REVIEW.md)
also assessed its accepted reply as cautious and useful. Live rejection followed
by acceptance was not observed: that path was established with native stub controls.
The [lifecycle guide](docs/LIFECYCLE.md) explains why 05 needs a controller outside
the imported candidate graph. Its [finite evaluation](docs/LIFECYCLE-EVALUATION.md)
separates original authoring from a deliberate mutation and model-authored repair.

Two [composition experiments](docs/GENERALIZATION-EVALUATION.md) completed four
unchanged one-shot authorings and four native live cases with 12 runtime calls:
[billing plus a digest](reports/20261004-open-tasks-integration/generalization-evaluation/series/billing-bulletin-packet/live/report.json)
and [two audience briefs](reports/20261004-open-tasks-integration/generalization-evaluation/series/two-audience-briefs/live/report.json).
Both answers per task used the same graph shape. For the briefs, the models shared
one product analysis and supplied writer inputs as inline objects; the two manual
native controls instead used shared analysis with combine steps or separate product
analyses. These specific alternatives passed; structural diversity between model
attempts was not observed. The models selected operations, bindings, reuse and
output structure within the existing catalog. Automatic prose checks are lexical;
the independent agent read found the short outputs faithful to their supplied facts.
This does not establish new business implementations or arbitrary workflow generation.
See the [runtime limitations](docs/PROFILE.md#research-harness-transport-modes).

The [local UI demonstration](reports/20261004-open-tasks-integration/ui-demo/run/verification.json)
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

Further reading: [glossary](docs/GLOSSARY.md),
[architecture and commands](docs/ARCHITECTURE.md),
[execution and acceptance reports](docs/REPORTS.md),
[profile](docs/PROFILE.md), [YAML generation](generation/README.md),
[verification](verification/README.md), and
[research questions](docs/RESEARCH-QUESTIONS.md). Dated records of individual
runs, including the [original analysis](docs/history/ANALYSIS.md), are frozen
under [docs/history/](docs/history/README.md) and are not current instructions.
Historical reports retain their old paths; see the
[file migration map](docs/MIGRATION-PATHS.json), which records the 2026-10-04
refactor and is not updated afterwards: its `ANALYSIS.md` entry now resolves to
`docs/history/ANALYSIS.md`.

The bounded historical generated-YAML/live replay command and its unpaid gate are
documented in [Frozen generated YAML with live operations](docs/GENERATED-LIVE.md).
