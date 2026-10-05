# support-review-packet runtime control

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a prepared control; copy or improve
it using the common profile and registered operations. This package tests
YAML execution and independent acceptance.

Create a Gantt workflow with logical id support-review-packet. Prepare a support review packet for one delivery ticket and its invoice batch. Use the ticket classifier to choose exactly one escalation or normal reply action draft at the more-than-two-days boundary. Independently validate the invoice batch and produce its total report. After both the selected action and invoice report finish, generate exactly one reply draft. Give reply.generate only the ticket id and text, previous null, and an empty feedback list. Check that draft against required_order_id and max_characters. Return exactly action, invoice_report, reply, and review, preserving the actual draft and check result. A failed draft check is a visible review outcome, not permission to revise, hide the draft, claim acceptance, or send a message.

Keep Callback fixture activation, revision 1, independent concurrency and
fail-fast errors. The common profile and complete operation catalog are in
`/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/workflow/bindings.yaml`.
Only modify the submitted YAML. Do not alter the compiler, runtime, installed
n8n or verifier. The verifier replaces sample inputs and checks real n8n
records plus independent business obligations. All outputs remain local
drafts or previews; no external side effects are permitted.
