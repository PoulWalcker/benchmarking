> Historical description before the 2026-10-04 refactoring. Paths and commands
> below are outdated; see [the current README](../README.md). This English
> translation retains the original experiment scope and historical instructions.

# SAPi config lab

A local research harness: YAML in the `sapi-lab/v0` profile → shared compiler →
import and execution in **real n8n 2.41.5** → independent verification in
**Harbor 0.21.0**. The baseline series checks prepared YAML within the supported
static profile. A separate series evaluates model-authored YAML for the same
three task families. The full Sapiens specification is not implemented.

Baseline: Sapiens commit `06ddd3333109cea8a2cb3071609070d7a3c0d3ff`.
At the recorded October 4, 2026 comparison, GitHub `main` matched it with no
substantial differences. The comparison, Haskell source, and SHA-256 hashes are
saved in `provenance/`. Execution always uses the pinned revision; a specification
update requires showing differences first. The original analysis is in
`ANALYSIS.md`; semantics and limits are in `PROFILE.md`.

Open discussion questions are in [RESEARCH-QUESTIONS.md](RESEARCH-QUESTIONS.md).
The model-authored YAML experiment runs with `./run-generation.sh`.
See [generation/README.md](../generation/README.md) for its description, limits,
and result structure. Generation uses a live model; model steps inside the
resulting workflow use stubs.

## One command

From the project directory:

```bash
./run.sh
```

The command runs local tests, builds an isolated image, checks HTTP contracts
with stubs, runs three Harbor oracle tasks and three nop controls, checks rewards,
and saves `reports/<UTC timestamp>/report.json`. Exit code 0 means all required
checks passed, not merely that the processes finished.

For a separate live series **after** passing the stub suite:

```bash
./run.sh --live
```

This first repeats the deterministic series. On success, it runs separate Harbor
oracle trials through the existing codex-exec wrapper. Results are in
`reports/<UTC timestamp>/live/report.json`. Eight live calls are expected:
two classifications and three calls for each of two reports. Invoices remain
deterministic Script operations. A failed live series returns a nonzero code
while preserving the successful stub report.

Use `--report-dir /absolute/new/path` to select a result directory. Existing
final reports are not overwritten. `--skip-build` is for development only;
normal reproduction should rebuild the image.

## Historical requirements

- Docker Engine / Docker Desktop / OrbStack, with Docker Compose.
- Python 3.10+, Node.js for fast local JS tests, and PyYAML.
- Harbor exactly `0.21.0` (`harbor --version`).
- Registry and Alpine package access for the first build.
- For `--live`: a working wrapper at `http://127.0.0.1:8765/run`, with configured
  Codex authentication. The wrapper supplies the model and account.

Historical installation commands:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
uv tool install harbor==0.21.0
```

n8n is pinned by version and OCI digest in `infra/Dockerfile`. Alpine is pinned
by digest, and direct apk dependencies by exact versions. Reports record actual
versions in `runtime-versions.txt` and the built image as `image_id`.
No production n8n secrets are copied.

## Checks

| Scenario | Positive cases | Negative cases and invariants |
| --- | --- | --- |
| Invoices | 38,000 AED minor units, other values/currencies, zero, maximum safe total | Empty batch, duplicate ID, mixed currencies, negative/fractional/unsafe amount, overflow |
| Ticket | Delays of 0, 2, 3, and 12 days; live mode uses 2 and 3 | Invalid days; exactly one branch operation completed, the other skipped; correct draft and ID |
| Two analyses | Original and new source materials | Empty source; independent analyses, join waits for both, quotations/data preserved, report reflects both sources |

Each Harbor task includes an instruction, environment, reference `solve.sh`, and
tests. Oracle places YAML at `/app/submission/config.yaml`. **The verifier itself**
compiles and executes it with its own inputs. A prepared JSON file claiming
success does not count. Expected values are computed independently without
importing operation implementations.

Additional checks cover an unknown operation, a cycle, and unsupported mandatory
parallelism. Deliberately corrupted workflows run successfully in n8n but produce
wrong outputs that the verifier must reject. Raw-result mutations test missing
nodes, lost data, and forged statuses. All three nop trials must receive reward 0.

Twelve HTTP probes use real n8n and a local **fake HTTP bridge**: valid response,
skip without a call, invalid input, wrong invocation ID, schema violation,
failed status, extra fields, malformed JSON, HTTP 503, an array instead of one
response, and timeout. These are transport tests, not live LLM calls.

## Isolation and execution evidence

Harbor creates a separate environment per task. Every test case uses a fresh
SQLite database and workflow ID. The real commands are `n8n import:workflow`
and `n8n execute --id ... --rawOutput`. Containers do not receive the Docker
socket, existing n8n volume, credentials, or user workflows. No workflow is
activated. Test Code nodes use n8n's in-process runtime; the user's production
runners are not involved.

The CLI can return process code 0 for a failed workflow. The adapter therefore
reads persisted `execution_entity`, `execution_data`, and node execution data.
It checks status, execution ID, Result, output, and join timing. An `execution_id`
is unique only within its database; complete run identity combines workflow ID,
execution ID, and case path.

Harbor tasks/jobs are temporarily staged under `/private/tmp` to avoid Docker
Desktop bind-mount restrictions on this system. Native Harbor artifacts are
copied to the project's report directory after completion.

Saved artifacts include:

- Local tests, image build, versions, and original Harbor logs.
- Oracle/nop rewards and independent acceptance reports.
- Per-case config, workflow JSON, mapping, import logs, n8n stdout/stderr,
  persisted execution metadata/data, result, and error.
- For live runs, separate trials and `bridge-audit.jsonl` with operation,
  invocation ID, duration, status, and model when provided by the CLI header.
  Wrapper stderr, credentials, and authentication tokens are not saved.

## Live connection

The existing user-managed wrapper listens on localhost:8765.
It accepts `POST /run` with `{prompt}` and returns
`{ok, output, stderr, exit_code}`. It remains unchanged. The separate
`bridge/agency_bridge.py` temporarily listens on `127.0.0.1:18765` and exposes
`/v1/agency/execute`. Docker accesses it through `host.docker.internal`.

The adapter accepts only catalog operations, builds a prompt from the binding,
validates JSON against `input_schema`/`output_schema`, and returns the matching
`invocation_id`. Secrets are not embedded in workflows. There are no automatic
retries, invalid-answer repairs, or stub replacements. The wrapper timeout is
180 seconds; the HTTP timeout is bounded by the workflow deadline. Live trials
explicitly raise that deadline to 600 seconds and record it in the case report.

Historical command for repeating live trials using a passed control report:

```bash
python3 scripts/run_live.py --stub-report reports/<run>/report.json --report-dir reports/<new-live-run>
```

## Historical compilation and local checks

```bash
python3 lab.py build
python3 lab.py compile configs/01-invoice-total.yaml --output /tmp/invoice.n8n.json
python3 -m unittest discover -v -p 'test_*.py'
```

`lab.py` reports `executed: false`: generating JSON does not mean execution.
`run-export.mjs` is a fast local JS driver for unit tests. It is **not n8n** and
is not evidence of real runtime execution. `scale_probe.py` is likewise not a
load test of real n8n.

A new scenario using existing operations needs YAML rather than a compiler
special case. A new operation needs a binding, handler, and contract. New
control semantics need a profile change and separate verification.

## Result boundaries

Supported: static DAG Pipeline/Gantt workflows, a shared operation catalog,
conditional skips, and `all_terminal` joins. `independent` means no dependency
between analyses; actual concurrent progress is not guaranteed. Inputs are
injected as fixtures through Manual Trigger. Event admission, Cron, and a
callback service are not implemented. `simulation: true` identifies fixture
injection, while `llm_mode` distinguishes stubs from real calls.

The correction loop (`04`) and daily digest lifecycle (`05`) remain drafts and
are rejected by the compiler. Sapiens harness, durable Agency execution, actor
memory/isolation, and crash recovery are not connected. The live verifier checks
specified facts, quotations, and data transfer. These bounded checks do not
prove complete model-generated text quality.
