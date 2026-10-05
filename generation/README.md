# Model-authored YAML

The second experimental iteration gives a model a task description, format,
profile, and operation catalog, then asks it to return YAML. The existing compiler,
real n8n, and independent verifiers evaluate that answer without editing it.

## Verified series

October 4, 2026: the public [results summary](../docs/RESULTS.md) records this
series and its report hash. Original prompts, generated YAML, and full results
are preserved locally under `reports/20261004-yaml-generation-1/`; they are not
included in Git or source distributions.
Three attempts each for invoices, routing, and research: **9/9 passed**.
The existing wrapper used `gpt-6-astra`. Answers were not edited; stderr showed
no recognized tool-call markers. Frozen-core hashes remained unchanged.
The series produced 69 real n8n execution records and 27 expected compiler
rejections of deliberately invalid definitions: 96 test cases, including negatives,
for nine configs, not 96 new tasks.

Before generation, 36 local tests, transport probes, and Harbor oracle/nop
controls passed. Interpretation limits are described below. Saved prompts are
part of that historical evidence; translating the current profile into English
changes future prompts and does not retroactively validate them with this series.

## Run

From the project root, after `uv sync --locked --extra harbor`, with Docker,
Harbor 0.21.0, and the existing wrapper at `http://127.0.0.1:8765/run` available:

```bash
./run-generation.sh
```

The default is three independent attempts per scenario: nine model calls.
These are repetitions without feedback, not a repair loop. For a short series,
use `./run-generation.sh --attempts 1`. `--report-dir PATH` selects a new report
directory; existing directories are never overwritten.

Before generation, the command runs the `sapi-lab harbor` control suite:
local tests, image build, transport probes, oracle, and nop. Generation calls
are not started if controls fail.

## Execution sequence

1. `src/sapi_config_lab/interfaces/generation/run.py` records the complete public
   source inventory, including all runtime/verifier modules, task data, prompts,
   packaging settings, and dependency locks. It uses the same inventory as the
   control suite; new modules are included automatically. Hashes remain fixed
   across answers, and local environment snapshots are excluded.
2. Prompts are assembled from `tasks.json`, `FORMAT.md`, `docs/PROFILE.md`, and
   `src/sapi_config_lab/core/bindings.yaml`. Complete prompts are saved in
   the task packages and agent artifacts.
3. Temporary Harbor packages contain neither `solution/` nor
   `environment/base.yaml`. Reference configs are removed from the running
   container. Checks are copied from `verification/` during packaging; there
   are no separately maintained copies of this task suite.
4. Harbor runs the custom `WrapperYamlAgent`. On the host, it sends the prompt
   once to the existing Python wrapper over codex-exec. Model settings are
   inherited from that wrapper; its implementation is not changed.
5. The exact answer is saved as `submission.yaml` and uploaded to
   `/app/submission/config.yaml`. No transformations are applied, including
   stripping Markdown fences. Failed answers are not replaced with reference YAML.
6. Harbor runs `test.sh`. The verifier substitutes its own inputs, compiles
   and executes the workflow in real n8n, and then checks the results.
7. Harbor saves the reward. Our runner assembles a report and verifies that
   the compiler, catalog, and verifier hashes remained unchanged.

**In this series, a live model generates configs, but LLM operations inside
those workflows use deterministic stubs.** This separates workflow construction
errors from execution-time model variability. Live operations in prepared
workflows are checked by the separate `./run.sh --live` command.

## Results

A new directory is created at `reports/<timestamp>-generation/`:

| File or directory | Purpose |
| --- | --- |
| `SUMMARY.md` | Attempt summary and links to YAML and checks |
| `report.json` | Full summary, failure stages, and limitations |
| `frozen-core.json` | Hashes of the experiment's fixed implementation |
| `task-packages/` | Exact Harbor packages and prompts for this series |
| `control/` | Separate control suite with prepared solutions |
| `jobs/generated/<trial>/agent/prompt.txt` | Prompt sent to the model |
| `jobs/generated/<trial>/agent/submission.yaml` | Exact model answer |
| `jobs/generated/<trial>/agent/generation.json` | Model, duration, hashes, and audit |
| `jobs/generated/<trial>/verifier/report.json` | Independent config verification |
| `jobs/generated/<trial>/verifier/cases/` | Imported graphs and actual n8n execution data |

Some files may be absent if a failure occurs before YAML is received or uploaded.
Failure stages are separated: generation/transport, YAML, compilation,
import/execution, and acceptance. Exit code 0 requires every attempt and control
to pass. Failed attempts are saved and are not automatically retried.

## What this experiment supports

This is a pilot on three known task families with a prepared, specialized
operation catalog. Success shows the model can construct configs under these
conditions. It does not establish generalization to new tasks, large graphs,
new operations, or the complete Sapiens specification. Checks are still written
by hand; there is no LLM-as-a-judge.

The verifier requires specific operations and logical scenario names, plus
actor names for the research report. These constraints are declared in the
public task/catalog. Steps can have custom names. Step IDs may be renamed and steps may be listed in any order. The independent
role/lineage contract still requires the task operations, inputs, guards and
outputs. The cycle control follows an actual dependency edge.

The generator receives only the assembled text. However, the existing wrapper
does not technically disable codex-exec tools: the no-file-access restriction
is a prompt instruction. The agent checks stderr for shell/tool/file/web markers
and rejects recognized tool use. This is observational auditing, not a secure
sandbox or proof that all hidden context is absent. Raw stderr is not retained;
only restricted metadata and a hash are saved. This pilot must not be described
as a benchmark protected against reference-solution access.

The wrapper does not return cost or a complete token breakdown. Its `tokens used`
counter is retained when available, without interpreting it as input/output token
counts or monetary cost. A few attempts do not provide a reliable success-rate
estimate outside this small series.

## Selected expansion tasks

The four additional tasks are opt-in and preserve the original three-task
default. Use the [bounded expansion run guide](../docs/SCENARIO-EXPANSION.md) for
ordered two-attempt authoring, private fixture overlays, frozen selection and
per-case live admission. Expansion calls require one shared durable series
ledger; a failed or unknown attempt blocks progression.
