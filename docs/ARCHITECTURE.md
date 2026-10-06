# Architecture

## System in one picture

The project tests a workflow definition through a sequence of responsibilities:

```text
Task source
    |
    v
Author definition (optional)
    |
    v
Compile --------> backend artifact
                    |
                    v
Execute --------> recorded evidence
                    |
                    v
Evaluate -------> acceptance / quality / report
```

A coordinator selects cases, binds fixtures, enforces budgets, assembles packages, and invokes these stages. Harbor provides isolated experiment orchestration around that flow.

The important boundary is not Harbor vs n8n. It is **definition -> artifact -> evidence -> verdict**.

## One scenario registry

Every scenario is `benchmarks/NN-<scenario>/`: a task, the environment it runs in, and the evaluator that judges it. All go through Author -> Compile -> Execute -> Evidence -> Evaluate, and every Harbor run goes through `coordinate/runs.py`, with paid calls reserved in `coordinate/ledger.py`.

| Concern | Fixture scenarios (01-09) | Imported simulator scenarios (10-11) |
| --- | --- | --- |
| Task source | `task.md` | pinned upstream AutoWFBench challenge + `authoring-notes.md` |
| Environment | evaluator-only fixtures in `cases.json` | upstream simulator started by the host per trial; candidate sees only tools |
| Evaluator | independent `verification/` in the container | pinned upstream scorer and judge on the host |
| Result | execution, acceptance, optional rubric quality | execution, acceptance (`execution_pass`), upstream score |

`coordinate/evaluation.py` is the one evaluation role; the two evaluators share the result shape, not their scoring semantics.

## Source layout

### Shared contracts

`src/sapi_config_lab/` contains the backend-neutral project contract:

- `profile.py` — parsing and validation of `sapi-lab/v0`.
- `bindings.yaml` — operation catalog and operation contracts.
- `contracts.py` — `WorkflowBackend`, compile options, compiled artifact, execution record.
- `evidence.py`, `paths.py`, `net.py` — shared infrastructure with narrow responsibilities.
- `autowfbench_source.py` and its manifest — pinned upstream source verification.

### Author

`src/sapi_config_lab/author/` asks a model to produce or repair a workflow definition.

Authoring stops at YAML. It does not compile or execute the workflow.

### Compile

`src/sapi_config_lab/compile/` converts a validated definition into a backend artifact.

For n8n this includes native node JSON, step mapping, and bounded refinement lowering. Compile code must not start n8n, open a runtime session, or decide whether the business task passed.

### Execute

`src/sapi_config_lab/execute/` owns runtime interaction:

- native n8n execution,
- Agency/model transport,
- Harbor/Docker helpers,
- the pinned AutoWFBench environment proxy.

Execution records what happened. It does not turn engine success into business acceptance.

### Evaluate

`src/sapi_config_lab/evaluate/` reads recorded results and produces host-side scoring or derived analysis.

The project's own scenario acceptance is intentionally separate under `verification/`. That verifier is packaged independently and cannot import the workflow implementation it is checking.

### Coordinate

`src/sapi_config_lab/coordinate/` is the composition layer:

- CLI dispatch,
- scenario and case selection,
- compile/execute sequencing,
- control, generation, live and benchmark series,
- Harbor task packaging,
- lifecycle coordination,
- source and budget gates.

Other stages should not depend on `coordinate/`.

## The independent verifier

`verification/` is intentionally duplicative at the business-rule level.

It recomputes expected results from fixtures and checks recorded native evidence without importing the compiler, runtime operations, or coordinator. Reusing the implementation under test would make a shared bug look like correctness.

A task run separates:

1. **engine execution** — n8n produced a runtime record;
2. **acceptance** — the verifier established required behavior and provenance;
3. **quality** — an optional rubric/judge scored the accepted or recorded output.

These values can disagree and must remain separately visible.

## Scenario packages and isolation

Project scenarios live under:

```text
benchmarks/NN-<scenario>/
  config.yaml
  task.md
  instruction.md
  cases.json
  scenario.json
  prompt-extension.md   # optional
```

Before a Harbor run, `coordinate/packages.py` assembles an isolated task package from the scenario, shared templates, runtime code, and verifier.

Candidate agents must not receive evaluator-only inputs such as reference solutions, private cases, or verifier internals. Packaging tests enforce this separation.

## Evidence model

Execution produces evidence first; evaluation comes later.

Typical flow:

```text
verifier plan
    -> runtime executes exactly the requested cases
    -> observation + native artifacts are frozen
    -> verifier checks completeness/integrity
    -> verifier computes acceptance
    -> optional rubric computes quality
```

`reports/` contains new local run output and is ignored by Git.

`evidence/` contains selected committed historical artifacts used to support durable claims. Historical evidence is immutable evidence of that run, not a mutable status page for the current project.

Source manifests and pinned revisions prevent results from silently claiming to describe a different implementation.

## Backend boundary

`WorkflowBackend` is the seam between shared workflow semantics and a concrete engine.

Today n8n is the only production backend. A second backend must define its own:

- supported capability subset,
- artifact compiler,
- execution evidence,
- provenance checks,
- semantics for branching, waiting, failures, and timing.

Do not introduce a universal IR, plugin registry, or inheritance tree merely to make a hypothetical backend look easy. Add abstraction when a second implementation creates a real shared shape.

## Durable design rules

- Compiler, executor, and evaluator are separate responsibilities.
- The verifier is independent by design.
- Orchestration may compose stages; stages should not reach back into orchestration.
- Upstream benchmark/specification sources are pinned and verified before trusted use.
- Live model dispatch is explicit, budgeted, and auditable.
- Historical run output is data, not architecture documentation.

## Terms

| Term | Meaning here |
| --- | --- |
| definition | validated `sapi-lab/v0` workflow YAML |
| artifact | backend-specific compiled workflow, currently n8n JSON |
| execution | one engine run and its recorded native evidence |
| acceptance | independent pass/fail business verdict |
| quality | optional non-binary rubric/judge result |
| oracle | positive control using a known reference submission |
| nop | negative control with no candidate submission |
| scenario | one project task family under `benchmarks/` |
| case | one fixture/input instance of a scenario |
