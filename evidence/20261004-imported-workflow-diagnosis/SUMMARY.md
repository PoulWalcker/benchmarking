# Imported workflow diagnosis

Date: 2026-10-04. Scope: read-only inspection of workflow `haUQEcB818FQjfkH` in the user's local n8n 2.41.5 instance. No workflow, model, Codex, or adapter was executed by this diagnosis. No existing workflow, container, or configuration was modified.

## Finding

The saved workflow is **Priority support brief — adapter required**, with 25 native nodes. Its four Agency HTTP nodes call `http://host.docker.internal:18765/v1/agency/execute`. Nothing is currently listening at that port. The existing Codex wrapper at port 8765 is a different service and cannot directly replace this endpoint.

Native execution 58 started at `2026-10-04 17:30:53.601 UTC` (`21:30:53.601 Asia/Dubai`) and failed at **Agency classify** with: `The service refused the connection - perhaps it is offline`. Demo start, Fixture, Prepare classify, and Guard classify succeeded. Execution 57 has the same failing node and message. Earlier execution 56 marked success ran **only Demo start**; it does not demonstrate successful execution of the complete workflow.

## Evidence

- The in-app browser reached the sign-in page; no UI authentication was attempted. Authoritative graph and execution records were read through SQLite opened with `OPEN_READONLY` inside the existing n8n container, restricted to the specified workflow.
- A TCP-only probe from `n8n-n8n-1` reported `host.docker.internal:18765 -> ECONNREFUSED` and `host.docker.internal:8765 -> connected`. It sent no HTTP request and invoked no model.
- Host listener inspection found Python listening at `127.0.0.1:8765`; there was no listener at 18765.
- The workflow contains embedded Code-node JavaScript. The observed nodes do not reference local scripts, `require`, `child_process`, or Codex commands. The successful initial Code nodes confirm code execution itself was available for this attempt.
- The four Agency nodes contain no credential assignment and send the prepared `request` object as JSON.
- The existing wrapper source, `/Users/pashvel/n8n/codex_bridge.py`, accepts `POST /run` with a `prompt`, then runs `codex exec --skip-git-repo-check`. TCP reachability does not establish current Codex authentication or successful model access; neither was exercised here.
- `src/sapi_config_lab/runtime/agency.py` accepts `{invocation_id, operation, inputs, actor?}` at `/v1/agency/execute`, loads the catalog contracts/prompts, constructs the wrapper prompt, validates the structured model output, and returns the Agency result envelope. Changing only the HTTP node's URL to the wrapper would not satisfy either request or response contract.

## Minimal remediation proposal

Run the compatible catalog Agency adapter on the existing port 18765 with the existing wrapper as upstream. This can preserve the workflow's current URLs and requires no graph change merely to restore transport. For a bounded, recorded live validation, use a **fresh** execution budget/deadline and new audit file; the completed experiment's consumed grant is not reusable. The adapter CLI also has an unbudgeted mode, but this diagnosis did not start it or recommend bypassing the experiment's limits.

After adapter readiness is established, a separate authorized full execution is required to confirm all remaining nodes, current Codex authentication, output contracts, and final business result. That has not been performed. The current graph was inspected directly; it was not assumed byte-identical to the prepared import file.

## Method and limits

Applied Matt Pocock's `diagnosing-bugs` and consulted `ask-matt`. The saved failures plus the minimal TCP refusal form the diagnostic evidence. Full workflow reproduction and fix phases were intentionally not performed because the requested scope was inspection and existing workflows must remain unchanged. No secrets or unrelated workflow records were exported. No commit or push was made.
