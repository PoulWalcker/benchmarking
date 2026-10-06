# Invoice total: YAML → n8n runtime trial

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a complete reference starting point;
copy it or improve its definition using the documented common profile and
registered operations. This first task tests runtime execution from YAML. It
does not evaluate an agent's ability to invent a workflow from a prompt.

Use a Script-only Pipeline to validate invoices, sum integer minor units and
return exactly `{total_minor, currency, invoice_count}`. Every invoice must be
counted once. The default three AED invoices total `38000`, count `3`. Inputs
will be replaced with additional batches, so do not hard-code this total.

Reject empty batches, duplicate IDs, mixed currencies, negative or fractional
amounts, numbers outside JavaScript's safe integer range, and a sum outside
that range. Invalid input must fail during real n8n execution without a
successful Result node.

Use the `invoices.validate`, `invoices.sum`, and `invoices.report` operations.
The source specification revision is
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`. Read `/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/bindings.yaml` for the experimental profile and operation contracts.
Keep the callback fixture activation, fail-fast errors and independent
concurrency. Compilation uses the shared compiler in `/app/lab/src/sapi_config_lab/compile/n8n.py`.

The verifier imports each compiled JSON into the pinned real n8n executable
in this isolated task environment, executes it, and checks results and raw
node execution records. Generating JSON alone does not satisfy the task.
Modify only the submitted YAML; do not change the compiler, operations,
runtime, installed n8n, or verifier. No existing user workflow is available
in this environment. Do not connect a Sapiens harness or external services.
