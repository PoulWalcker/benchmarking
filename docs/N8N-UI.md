# View and run workflows in local n8n

Run these commands from the repository root with local n8n 2.41.5 running at
`http://localhost:5678`. The default Docker container is `n8n-n8n-1`; use
`--container NAME` if yours differs. The browser still requires your existing
n8n login. These commands do not recover or reset its password.

## View the graphs

```sh
uv run --locked sapi-lab ui open --all
```

This compiles and imports the eight standalone examples as inactive workflows,
prints their direct editor links, and opens the n8n landing page. It makes no model
calls and starts no workflow. There is no manual JSON import or export step.
Config 05 is reported separately as requiring the lifecycle controller; it is
not silently omitted or presented as a complete standalone graph.
In this checkout, you can [inspect the imported 09 graph](http://localhost:5678/workflow/2eade6ecad1d481f)
directly after signing in.

To view one config, use `uv run --locked sapi-lab ui open configs/09-priority-support-brief.yaml`;
that opens the selected graph directly. Add `--no-browser`
to print links without opening a browser tab.
Repeating the command for unchanged YAML reuses the project-owned graph. If n8n
or a user changed that copy, its link still opens with an explicit
`reused_edited` status; the edits are preserved. Use `--new-copy` for a fresh
YAML copy. A removed graph requires `--new-copy`. Existing workflows are not
overwritten.

## Prepare a manual live run

```sh
uv run --locked sapi-lab ui open configs/09-priority-support-brief.yaml --live
```

This creates a fresh inactive copy, derives its model-call ceiling, and starts
the foreground Agency adapter. Wait for its readiness output, keep the terminal
open, then press **Execute workflow** in n8n. The command itself does not execute
the graph. `--all` is view-only; choose one config for a live session.
Use the fresh editor link printed by `--live`: pressing Execute in a previously
viewed LLM graph does not start its required adapter.

LLM workflows require the existing Codex wrapper at `http://127.0.0.1:8765/run`.
If no wrapper identity is saved, add `--wrapper-evidence PATH` pointing to an
inspected identity record; the validated path is saved locally and reused.
This checkout has its previously validated record saved:
`evidence/20261004-open-tasks-integration/wrapper-identity.json`.
The final check found that the Codex configuration has changed since that
inspection. Live preparation currently stops with `Wrapper/config identity
changed` before importing a copy. A fresh inspection and matching identity
record are required before the live command can proceed.
That record identifies the actual wrapper/configuration; it is not a credential
or a template to fill with assumed values. Script-only configs 01 and 06 need
neither the wrapper nor the Agency adapter.

Live preparation defaults to 600 seconds for both the workflow deadline and the
foreground session. `--seconds N` changes that limit, up to 3600 seconds. The
original YAML remains unchanged; its bytes and the explicit deadline overlay
are recorded with the prepared copy. The adapter's lifetime begins when it
starts, so execute promptly. Ctrl-C stops the adapter. For LLM workflows, one
session admits one execution; repeat the live command for another attempt.

The call ceiling is derived from the selected graph, including possible branches
and bounded refinement attempts. You do not need to choose `--max-attempts` for
`ui open`. Opening a live session checks readiness without calling the model;
pressing Execute on an LLM graph can make real model calls.

## Inspect the result

Open the execution in n8n, then select nodes to inspect their inputs, outputs,
errors and timing. The final Result node exposes the workflow output. A green
execution establishes engine success; it does not replace the project's
independent business acceptance checks. See the [report field guide](REPORTS.md)
and the [recorded evaluations](../README.md#evidence-and-limits).

Config 04 contains its bounded attempts and early exits in one native graph.
Config 05 additionally requires the [durable lifecycle controller](LIFECYCLE.md)
for registration, Callback testing, rebuilds, revision release and Cron admission.
Importing a candidate graph alone does not implement that lifecycle.

## If opening or running fails

- A login page requires the existing n8n account; automatic import does not sign
  you in. Password access is a separate prerequisite.
- A stopped or unreachable wrapper affects live LLM sessions; viewing graphs and
  running Script-only workflows do not require it.
- The older `ui serve` command expects one prepared workflow directory containing
  `prepared.json`, not the parent of several directories. A missing prepared-file
  error does not mean the workflow ran. Use `ui open CONFIG --live` for the normal
  path; it manages preparation and admission together.

The earlier [manual demonstration and execution 61](http://localhost:5678/workflow/a306069acac34f50/executions/61)
remain available after login. Its helper is stopped; viewing its history makes
no model call and does not reopen that completed session.
