# Frozen generated YAML with live operations

This command implements the prepared [execution task](history/generated-yaml-live-execution.md).
It replays the three preselected historical submissions unchanged, first through
all stub verifier cases and then through seven live cases. It makes zero new
YAML-generation requests. This implementation is separate from recorded
[historical results](history/RESULTS.md); a successful fresh run is required for a live claim.

Keep sources unchanged from the control run through final execution. Every output
directory must be new. The source manifest covers implementation, tests, prompts,
catalog, verifier and public documentation. The live command requires the same
source inventory and image ID as the successful control report.

```bash
# Fresh unpaid controls: local tests, 12 real n8n transport probes, oracle/nop.
./run.sh --report-dir reports/generated-live-controls-1

# Build and validate the approved historical selection; no model call.
uv run --locked python -m sapi_config_lab.coordinate.replay \
  --source-report reports/20261004-yaml-generation-1/report.json \
  --output reports/generated-live-selection-1.json

# Optional standalone unpaid gate: 32 cases, 23 n8n executions, 9 compile rejections.
uv run --locked --extra harbor sapi-lab live \
  --stub-report reports/generated-live-controls-1/report.json \
  --submissions-manifest reports/generated-live-selection-1.json \
  --report-dir reports/generated-live-preflight-1 \
  --preflight-only

# Explicit live phase; repeats the unpaid replay gate before starting the bridge.
uv run --locked --extra harbor sapi-lab live \
  --stub-report reports/generated-live-controls-1/report.json \
  --submissions-manifest reports/generated-live-selection-1.json \
  --wrapper-evidence reports/wrapper-inspection-1.json \
  --report-dir reports/generated-live-execution-1
```

The explicit live command already runs the full unpaid replay gate; a separate
`--preflight-only` run is optional and is useful for inspection before authorizing
live execution.

Without a submissions
manifest, the existing `sapi-lab live --stub-report ...` command uses reference
YAML. Fresh generation remains the separate `run-generation.sh` experiment.

## Wrapper prerequisite

Before the explicit live command, inspect the unchanged existing wrapper's
source/configuration read-only. Confirm the endpoint dispatches to `codex exec`,
has no response substitution and performs no wrapper retry. Record the inspection
in a private JSON file; the runner verifies its file identities before and after
phases. This is an operator inspection of a trusted wrapper, not independent
provider attestation. No wrapper process or configuration is changed.

The inspection record has this shape (replace example values with observed
identities; paths may be absolute in this private file):

```json
{
  "schema": "sapi-lab-wrapper-identity/v1",
  "endpoint": "http://127.0.0.1:8765/run",
  "dispatch": "codex-exec",
  "response_substitution": false,
  "wrapper_retries": 0,
  "provider_internal_retries": "unknown",
  "model": "gpt-6-astra",
  "files": [
    {"path": "path/to/inspected-wrapper.py", "sha256": "observed SHA-256"},
    {"path": "path/to/inspected-cli-config.toml", "sha256": "observed SHA-256"}
  ]
}
```

Only identities and allowlisted metadata are saved; do not copy credentials,
raw stderr, session contents or full configuration into public reports. The
optional `--wrapper-evidence` argument also works in unpaid preflight, where it
performs file checks without contacting the wrapper.

## Budget, stopping and interpretation

The bridge durably records an attempt immediately before each outgoing wrapper
call. The run permits at most eight attempts, with at most two each for
`ticket.classify`, `research.product`, `research.marketing` and `research.write`.
Invoice has zero runtime model calls. Requests have no automatic retries or
redirect following. Duplicate invocation IDs, invalid input, unexpected
operations, wrapper failures, malformed outputs and budget overflow latch the
adapter closed. The driver stops scheduling after a failed trial or evidence check.
A timeout can leave upstream work running; its outcome remains unknown and a
later diagnostic must use a new directory, after read-only activity inspection.

Native Prepare/Guard/Agency/Restore records must agree with the saved graph,
execution IDs, independently accepted result and append-before-dispatch audit.
The final correlation table links submission/configuration identity to input,
prompt and response hashes. Hashes of structured inputs/requests/responses use
UTF-8 JSON with sorted keys, compact separators, unescaped Unicode and finite
numbers only. Prompts retain their established bytes and use a direct UTF-8 hash.

`counts.agency_http_attempts`, `counts.wrapper_attempts` and
`counts.wrapper_completions` are distinct. `provider_call_count` stays `null`
without provider receipts. A scoped passing report establishes confirmed real
wrapper/model completions; it does not establish a count of paid provider requests
or a currency cost. Available CLI token/retry markers are limited observations.

Private output includes original YAML/provenance copies, task-package hashes,
source snapshots around each phase, native execution files, executed config hashes,
audit, correlation and failure categories. The verifier declares its two intentional
configuration transformations: fixture input replacement and the live 600-second
deadline. Original YAML bytes remain separately preserved. Partial failures retain
evidence and a failed final report; unavailable counts remain `null`. Output trees
stay excluded from Git, Docker contexts, wheels and source archives.
