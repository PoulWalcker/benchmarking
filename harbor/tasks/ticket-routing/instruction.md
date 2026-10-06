# Ticket routing: YAML → n8n runtime trial

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a complete reference starting point;
copy it or improve its definition using the common profile and registered
operations. This is a runtime trial from a supplied YAML definition, not a
prompt-to-workflow benchmark.

Classify a delivery ticket as `high` only when its integer `days_overdue` is
greater than `2`; values `0`, `1` and `2` are `normal`. Execute exactly one
logical branch: high → escalation draft, normal → ordinary reply draft.
Return exactly `{ticket_id, action, mode}` with the unchanged ticket ID,
`action: escalate` or `action: normal_reply`, and `mode: draft`. Do not send
any message. Reject negative and fractional overdue days.

Use `ticket.classify`, `ticket.escalation_draft`, `ticket.normal_draft`, and
`branch.select_one`. The join must wait for both branch statuses, retain the
skipped status and receive no output value from the skipped operation.
The backend may execute a guarded wrapper for the skipped branch; that is
not a logical operation invocation. Both the high case and normal boundary
must work when the verifier replaces the input ticket.

The source specification revision is
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`. Read `/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/bindings.yaml`. Use Gantt, callback fixture activation, fail-fast
errors and independent concurrency. LLM calls default to deterministic
stubs; a separately configured live trial uses the same operation contracts.

The verifier imports and executes the generated JSON in a pinned, isolated
real n8n and checks raw node records, statuses and final data. JSON generation
or a locally simulated execution is insufficient. Modify only the submitted
YAML; do not change compiler/runtime/operations, n8n or verifier. No existing
user workflows are available. Do not connect a Sapiens harness.
