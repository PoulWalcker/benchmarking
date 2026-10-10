# Ticket Routing: sapi-lab/v0 Gantt -> native Harbor -> n8n

Submit one valid sapi-lab/v0 YAML workflow to `/app/submission/config.yaml`.
Construct a Gantt that classifies a delivery support ticket, selects exactly one
high/normal response branch, and joins the branches into one final result.

Routing policy: `days_overdue > 2` => high priority and `escalate`;
otherwise => normal priority and `normal_reply`.
Do not claim to have contacted any customer: all actions are *draft* actions.
Return exactly `{ticket_id, action, mode}` with `mode: draft`.
Invalid negative or non-integer days_overdue values must fail, not produce a result.

Use only declared operations in `/app/public/bindings.yaml` and the published
`sapi-lab/v0` profile. Submit only the YAML. You may not modify compiler,
operations, runtime, test inputs, verifier, Docker assets or trusted evaluation.
The verifier supplies independent ticket fixtures and executes the compiled graph
in the pinned real n8n runtime. A valid YAML file alone is not sufficient.
No external API access or customer messaging is required.
