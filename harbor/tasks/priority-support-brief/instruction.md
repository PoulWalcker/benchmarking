# priority-support-brief runtime control

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a prepared control; copy or improve
it using the common profile and registered operations. This package tests
YAML execution and independent acceptance.

Create a Gantt workflow with logical id priority-support-brief. Classify a delivery ticket and produce exactly one escalation or normal reply action draft using the existing more-than-two-days rule. After selecting the action, prepare an internal product-and-market brief only for high priority tickets: analyze the supplied product and marketing materials independently, combine both analyses, then write the brief. Every step of this optional region must use the unconditional classification decision as its guard. Return exactly action and brief; brief is null for normal priority. For normal priority, none of the optional research operations may be called, including when those source strings are empty. Preserve both sources and their evidence for high priority. All outputs remain drafts; send nothing.

Use exactly one occurrence of each of ticket.classify, ticket.escalation_draft,
ticket.normal_draft, branch.select_one, research.product, research.marketing,
research.combine and research.write. The dependency list must contain exactly these nine
edges between operation occurrences: ticket.classify -> ticket.escalation_draft; ticket.classify ->
ticket.normal_draft; ticket.escalation_draft -> branch.select_one; ticket.normal_draft
-> branch.select_one; branch.select_one -> research.product; branch.select_one ->
research.marketing; research.product -> research.combine; research.marketing ->
research.combine; research.combine -> research.write. Guard each of research.product,
research.marketing, research.combine and research.write by comparing the unconditional
ticket.classify step's priority to high. The research.write step must be the only
terminal step. Step IDs and step list order are your choice. Do not add dependency
edges, including redundant ones.

Keep Callback fixture activation, revision 1, independent concurrency and
fail-fast errors. The common profile and complete operation catalog are in
`/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/workflow/bindings.yaml`.
Only modify the submitted YAML. Do not alter the compiler, runtime, installed
n8n or verifier. The verifier replaces sample inputs and checks real n8n
records plus independent business obligations. All outputs remain local
drafts or previews; no external side effects are permitted.
