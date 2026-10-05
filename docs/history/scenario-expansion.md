# Scenario expansion: preparation backlog

Status update, October 4, 2026: **all four bounded task families have recorded
native live evidence**. The [original series](../../evidence/20261004-scenario-expansion-integration/SUMMARY.md)
completed SC-01/02 and stopped at SC-03; a [separate corrected series](../../evidence/20261004-corrected-expansion-integration/SUMMARY.md)
completed SC-03/04. The original rejection is preserved. Independent agent content
review is recorded separately; human review and the proposed third-authoring
team-pilot gate are not claimed complete. That gate is project policy, not a
specification or statistical rule. See the [run guide](../SCENARIO-EXPANSION.md)
and [current research status](../RESEARCH-QUESTIONS.md#task-status-october-4-2026).

The original backlog below retains preparation-time capabilities, acceptance
criteria and budgets. Its unsupported-refinement/lifecycle statements describe
that earlier static scope; later extensions are evaluated separately. Future
commits must use Conventional Commits.

Keep the common `sapi-lab/v0` profile and specification revision
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`. This backlog asks whether a model can
compose existing operations for four new tasks. It does not ask whether the
compiler can implement arbitrary business processes.

The first dependency is the existing three-family
[generated YAML with live execution experiment](generated-yaml-live-execution.md).
Complete its frozen-artifact ingress, controls, audit, and budget gates before
starting expansion runs. Its live baseline and this expansion are separate
series with separate denominators. Implement the generic preparation tickets
below before authoring or evaluating the first new scenario.

## Evidence and current interface

The [recorded results](../RESULTS.md), [generation guide](../../generation/README.md),
and [verification guide](../../verification/README.md) establish the current
scope: `invoice-total`, `ticket-routing`, and `competitor-report`. Historical
model-authored YAML passed 9/9 attempts with deterministic runtime stubs. That is
not generated-YAML/live-runtime evidence. The coordination handoff reports the
last refactor integration as 62 local tests and 35 fresh n8n executions; those
numbers were not rerun or independently audited in this preparation. Neither
baseline establishes the new scenarios below. Preserve the original local
reports and identify future results by new paths and hashes.

The [profile](../PROFILE.md), [catalog](../../src/sapi_config_lab/workflow/bindings.yaml),
[validator](../../src/sapi_config_lab/workflow/profile.py),
[compiler](../../src/sapi_config_lab/runtime/n8n/compiler.py), and
[runtime helpers](../../src/sapi_config_lab/runtime/n8n/runtime-fragment.js)
give the following limits.

| Area | Current behavior and consequence for this backlog |
| --- | --- |
| Static size | Validation requires a nonempty DAG but sets no step-count ceiling. The compiler emits one logical occurrence per distinct step ID and does not forbid reusing an operation. Absence of a ceiling is not evidence that arbitrary sizes work. |
| Arrays | One envelope contains arrays as values. There is no dynamic map, per-record n8n item execution, or variable step collection. The two ledgers below are two explicitly named input fields. |
| Joins | Each step with multiple direct predecessors requires `all_terminal`; the compiler emits an append Merge with that input count. Multiple terminal steps also get an automatic final Merge. This plan initially uses at most two direct inputs per join and at most two terminal steps. |
| Skip | A skipped step still carries an envelope and a status, with no operation output. `optional_ref` yields null only for an explicitly skipped step; a missing envelope/result is an error. It is not a fallback/default-value operator. |
| Guards | `when` has one strict `ref` plus exact `eq`. A conditional producer requires `optional_ref` when referenced, while `when` itself cannot use that form. A later guarded region can reuse an unconditional ancestor's decision, as in SC-04; it cannot safely inspect a possibly skipped producer with a new downstream guard. Use scalar decisions, not structural object comparisons. |
| Output | `resolveValue` already handles literal object/array composition containing references. `generation/FORMAT.md` currently describes only a final step reference. Packet outputs require a public authoring clarification, not a new compiler special case. |
| Data contracts | LLM inputs/outputs have a closed supported JSON Schema subset. Reference validation checks the first output field, not full nested compatibility. Script operations vary in their domain checks; `invoices.sum` assumes prior validation, and `digest.prepare` checks nonempty input and duplicate IDs but not every article field. |
| Execution | Callback fixture admission only; `independent` permits sequential work; one fail-fast run. Required parallelism, refinement, lifecycle, and Cron are explicitly rejected. No retries, durable recovery, external event waits, or isolated actor processes are available. |
| Existing unused bindings | `reply.generate`, `reply.check`, and the three `digest.*` operations are registered. Their containing [refinement](../../configs/04-revise-answer.yaml) and [lifecycle](../../configs/05-digest-lifecycle.yaml) examples remain unsupported. Reusing their operations in a new ordinary DAG does not validate those extension drafts. |

The [synthetic scale probe](../../tests/support/scale_probe.py) builds a static
binary tree and runs the minimal local JS driver; it explicitly does not run
n8n. It also nests untyped `research.combine` results without sending that tree
through the stricter writer input contract. It cannot establish large live
graphs, arbitrary fan-in, model quality, or schema-compatible composition.

## Generic preparation tickets

These tickets belong at existing module interfaces described in
[the architecture](../ARCHITECTURE.md). Keep business acceptance independent of
compiler/operation implementations; keep the runtime interface free of expected
answers. Do not add scenario IDs, fixture answers, or branching shortcuts to the
compiler. No compiler extension is currently required by the proposed graphs;
that is a source-based hypothesis until the topology controls pass.

| Ticket | Depends on | Implementation scope and acceptance |
| --- | --- | --- |
| PREP-01: observe logical occurrences | Linked generated/live baseline design | Generalize the verifier's observation interface to distinct step occurrences, retaining operation as an attribute. [Business checks](../../verification/business.py) and [native provenance](../../verification/n8n_provenance.py) currently key by operation and reject repeats; rejection lookup also expects one instance. Bind independently declared business roles to submitted step IDs through the submitted graph, declared operation, and expected input origin, allowing renamed IDs. Reject ambiguous, missing, extra, or duplicate occurrences. Check native execution, exact status/output presence, per-edge order, and live evidence for every LLM kind, including reply/digest. Preserve existing three-family behavior and reject swapped same-operation occurrences. Use a bounded scenario registration mechanism shared by packaging/selection without a plugin framework; unknown scenarios must fail closed. |
| PREP-02: make graph controls independent of list order | PREP-01 | Replace the existing last-step-to-first cycle mutation with a reverse edge along an actual dependency path, or a two-edge cycle between distinct valid nodes when no path exists. Current [mutation code](../../verification/verify.py) need not create a cycle in a multi-root DAG. Test name/order changes, two roots/two terminal outputs, a shared ancestor at a binary join, and repeated guards across a skipped region in actual pinned n8n. Keep the JS driver useful but label it separately. A removed input edge/token must never be accepted as an optional skip; native failure, absent Result, and verifier rejection are distinct outcomes. |
| PREP-03: publish the existing composition surface | PREP-01 and PREP-02 | Clarify [FORMAT](../../generation/FORMAT.md) with nested output maps, projected input objects, and guards using an unconditional decision; distinguish these from new expression syntax. Preserve the same full profile/catalog for every task. Extend canonical [task packaging](../../src/sapi_config_lab/experiments/tasks.py), task descriptions, fixtures, and independent criteria for one scenario at a time. Remove the topological-list-order evaluation restriction only after PREP-02. Verify packages and frozen inventories without exposing reference YAML or verifier expectations to authoring. |
| PREP-04: adopt bounded new-scenario execution | PREP-01 through PREP-03 and linked live baseline passing | Reuse the linked plan's frozen generated YAML ingress, audit, native evidence, call admission limits, and failure accounting. Permit a named new scenario only after its prepared control and generated stub gates pass. Add scenario-specific acceptance predicates outside the compiler. Do not extend a partially successful paid run or silently retry it. |

If an experiment exposes a runtime defect, stop that series and open a generic
compiler/runtime ticket with a smallest reproducer using at least two distinct
graphs. For example, a failure to wait for two terminal envelopes is a terminal
join defect, not a reason to introduce `dual-ledger-closeout` handling. A missing
condition expression, dynamic collection, refinement loop, or durable lifecycle
is a new-semantics proposal and is outside these four tasks. Existing explicit
rejection must remain until a separate profile/validator/runtime/evidence design
is approved and verified.

## Ordered scenario tickets

The order increases different difficulties, not just node counts. All tasks
use revision 1, fixture Callback activation, `independent`, fail-fast behavior,
and the common catalog. Public prompts below are task paragraphs to append to
the common authoring material; no reference YAML or private expected values
belong in that prompt. Step labels in this plan illustrate roles, not mandated
IDs. All fixture values shown here are public authoring examples.

| Order | Task | Logical steps | LLM occurrences per valid run | New question |
| --- | --- | --- | --- | --- |
| SC-01 | Dual-ledger closeout | 6 | 0 | Repeated operations, independent roots, two final outputs |
| SC-02 | One-shot support packet | 9 | 2 | Compose invoices, routing, and an honestly reported draft check |
| SC-03 | Bulletin-to-market brief | 7 | 4 | Carry evidence through a derived source and two LLM stages |
| SC-04 | Priority-gated support brief | 8 | 4 high / 1 normal | Skip a complete multi-stage region, including its join and writer |

### SC-01 — Dual-ledger closeout

Dependencies: PREP-01 through PREP-04. This is a static pair of separately
validated ledgers, not a currency conversion or dynamic batch loop.

**Human task prompt**

> Create a Pipeline with logical id dual-ledger-closeout. Close the domestic
> and export invoice ledgers independently. Validate each ledger, sum its
> validated amounts, and create its own report. Return exactly domestic and
> export, each containing total_minor, currency, and invoice_count. Never mix
> the ledgers or convert currencies. Either invalid ledger must fail the whole
> run without a successful result. Use the registered invoice operations for
> both ledgers and derive both reports from the supplied values. The input
> fields are domestic_invoices and export_invoices. No model call is needed.

**Public fixture**

```json
{"domestic_invoices":[{"id":"D-A","amount_minor":1251,"currency":"AED"},{"id":"D-B","amount_minor":0,"currency":"AED"}],"export_invoices":[{"id":"X-A","amount_minor":73,"currency":"EUR"},{"id":"X-B","amount_minor":210,"currency":"EUR"}]}
```

**Graph:** `domestic_validate → domestic_sum → domestic_report` and
`export_validate → export_sum → export_report`. Both chains use
`invoices.validate/sum/report`. There are no cross-chain edges. Compose
`{domestic: <domestic report ref>, export: <export report ref>}` as workflow
output; the compiler's final join waits for both terminal envelopes.

**Independent verifier:** compute each total with Python integer arithmetic;
check count/currency, exact input preservation in each validation output, and
the correct ledger lineage through each sum/report. Observe six distinct
occurrences even though only three operation names appear. Both terminal nodes
must finish before Result, with no cross-ledger intermediate data at a chain's
own operation nodes. No Agency HTTP execution is allowed in either mode.

**Controls:** positive base fixture plus a fresh different-size pair with
different currencies and a zero-valued entry. Reverse invoice order: each
report is unchanged. Add one uniquely identified zero invoice to one ledger:
only its count changes. Modify one amount: only that ledger's total changes.
Make only the domestic ledger invalid, then only the export ledger invalid,
using duplicate IDs, mixed currency, and safe-integer aggregate overflow.
Require rejection at the intended occurrence and no successful Result; do not
require the unrelated chain never to have begun under fail-fast scheduling.
Swapped report keys, a constant report, a missing terminal envelope, and an
operation-keyed trace that loses one occurrence must all fail acceptance.

### SC-02 — One-shot support packet

Dependencies: SC-01's stub/evidence gate. This exercises the reply bindings
without claiming refinement or accepted-answer production.

**Human task prompt**

> Create a Gantt workflow with logical id support-review-packet. Prepare a
> support review packet for one delivery ticket and its invoice batch. Use the
> ticket classifier to choose exactly one escalation or normal reply action
> draft at the more-than-two-days boundary. Independently validate the invoice
> batch and produce its total report. After both the selected action and
> invoice report finish, generate exactly one reply draft. Give reply.generate
> only the ticket id and text, previous null, and an empty feedback list. Check
> that draft against required_order_id and max_characters. Return exactly
> action, invoice_report, reply, and review, preserving the actual draft and
> check result. A failed draft check is a visible review outcome, not permission
> to revise, hide the draft, claim acceptance, or send a message.

**Public fixture**

```json
{"ticket":{"id":"S-31","text":"Please check order A9137, delayed three days.","days_overdue":3},"invoices":[{"id":"B-1","amount_minor":1251,"currency":"AED"},{"id":"B-2","amount_minor":349,"currency":"AED"}],"required_order_id":"A9137","max_characters":120}
```

**Graph:** `classify → {escalate, normal} → select` using the existing ticket
operations and `branch.select_one`; independently
`validate → total → report` using invoice operations; `{select, report} → draft
→ check` using `reply.generate/check`. Both multi-input steps have
`all_terminal`. Supply `reply.generate.ticket` as the object projection
`{id: <input id ref>, text: <input text ref>}`: the classification ticket's
`days_overdue` is forbidden by the reply operation's closed input schema.
The final packet references select, report, draft, and check. This is nine
static steps and a single terminal check.

**Independent verifier:** apply the invoice and routing rules independently,
then recompute the reply review from the actual returned text. `pass` is true
exactly when the order ID occurs and the text fits the limit; verify the errors
and their prescribed order. The current check uses JavaScript string length,
so an independent implementation must count UTF-16 code units rather than
assume Python character length. Check draft inputs, one generation occurrence,
and the join after select/report; the response must contain the real draft and
its actual check. The current deterministic stub's first reply omits the order
ID, so `review.pass=false` is an expected successful packet outcome. A live
reply may pass or fail; this task proves honest review reporting, not reply
helpfulness or guaranteed quality.

**Controls:** positive high/normal tickets with alternate invoice values and
order IDs; a very small positive limit makes a failed review visible. Changing
only an invoice amount must change the invoice report while preserving the
routing decision. Changing only days 2→3 changes the selected action. In stub
or deterministic endpoint controls, reducing the limit against the same fixed
reply changes the recomputed review appropriately; do not demand identical
prose across two live calls. Duplicate invoices or invalid overdue values must
fail without Result. A wrong order ID produces review failure, not runtime
failure. Reject a fabricated `pass=true`, a dropped errors field, a constant
invoice report, missing selected action, and any second draft attempt. A
full-ticket passthrough to reply must fail its input schema before a live call.

### SC-03 — Bulletin-to-market brief

Dependencies: SC-02's stub/evidence gate. This is a Callback preview using
digest operations, with no lifecycle or schedule.

**Human task prompt**

> Create a Gantt workflow with logical id bulletin-market-brief. Prepare the
> supplied product bulletins, summarize them, and retain the digest as a
> preview with its article IDs. Analyze product capabilities from that
> digest's actual text. Independently analyze the supplied marketing material,
> combine those analyses, and write a short source-grounded brief. Return
> exactly digest and brief. Preserve every named capability, audience, and
> channel in the supplied small fixture; retain evidence through the analyses,
> join, and final brief. Product evidence must quote the digest text actually
> passed to its analysis. Do not fetch outside material, publish, schedule, or
> invent product claims. Use the registered digest and research operations.
> Assign digest summarization, product analysis, marketing analysis, and
> writing to four separate logical actors, each allowed only its own model
> operation; actor names are your choice. Use exactly one occurrence of each
> of digest.prepare, digest.summarize, digest.preview, research.product,
> research.marketing, research.combine and research.write. The dependency list
> must contain exactly these six edges between operation occurrences: digest.prepare ->
> digest.summarize; digest.summarize -> digest.preview; digest.summarize ->
> research.product; research.product -> research.combine; research.marketing
> -> research.combine; research.combine -> research.write. The
> research.product material input must reference the text field directly from
> the digest.summarize step. The digest.preview and research.write steps must
> be the only two terminal steps; preview has no outgoing dependencies. Step
> IDs and step list order are your choice. Do not add dependency edges,
> including redundant ones.

**Public fixture**

```json
{"articles":[{"id":"BL-1","title":"Offline inventory","text":"The product supports offline inventory."},{"id":"BL-2","title":"CSV export","text":"The product supports CSV export."}],"marketing_material":"The service targets rural cooperatives through printed catalogues."}
```

**Graph:** `prepare → summarize → preview` using `digest.*`;
`summarize → product` using `research.product` on `summarize.text`;
an independent `research.marketing` root; `{product, marketing} → combine →
write` using `research.combine/write`. The preview and writer are two terminal
steps. Product and marketing analyses have separate logical actors with their
allowed operations; declare separate digest and writer actors as metadata too.
The source task does not require or establish process isolation.

**Independent verifier:** require both supplied article IDs exactly once and
no unknown IDs in this deliberately bounded fixture, preview mode, unchanged
prepared articles, and exact digest-to-product input transfer. Check the two
named capabilities in the digest, product analysis, and final report, and the
audience/channel in marketing analysis and final report with a frozen explicit
lexical contract. Product evidence must be a nonempty contiguous substring of
the actual digest; marketing evidence must quote its own supplied source.
Combine must preserve both analyses, and writer evidence must equal their
excerpts in product/marketing order. The digest is a derived source: quoting it
does not alone prove faithfulness to original bulletins. Fixture fact checks
and a recorded human review of live text remain separate acceptance evidence.

**Controls:** second positive fixture uses disjoint article IDs, capabilities,
audience, and channel. Reordering articles preserves the ID set and coverage,
without requiring identical live prose. Replacing one capability must replace
its required fact and remove an explicitly forbidden old capability; changing
marketing alone must not change the expected product source lineage. Use empty
articles, duplicate article IDs, malformed article fields, and empty marketing
as deterministic negative controls with the rejecting occurrence recorded.
Because `digest.prepare` does not validate every field, malformed fields may
fail later at summarize's schema check; do not claim prepare rejected them.
Reject swapped evidence, an unknown/missing article ID, unrelated prose retaining
valid excerpts, lost digest text at product input, or an invented capability from
the controlled disjoint fixture. Lexical acceptance is bounded, not proof of
arbitrary factual quality. If live digest omits required coverage, record task
failure; do not strengthen its catalog prompt mid-series.

### SC-04 — Priority-gated support brief

Dependencies: SC-03's stub/evidence gate and the repeated-guard native probe.
This is a larger composition of existing equality guards and skip envelopes,
not a new branch construct.

**Human task prompt**

> Create a Gantt workflow with logical id priority-support-brief. Classify a
> delivery ticket and produce exactly one escalation or normal reply action
> draft using the existing more-than-two-days rule. After selecting the
> action, prepare an internal product-and-market brief only for high priority
> tickets: analyze the supplied product and marketing materials independently,
> combine both analyses, then write the brief. Every step of this optional
> region must use the unconditional classification decision as its guard.
> Return exactly action and brief; brief is null for normal priority. For
> normal priority, none of the optional research operations may be called,
> including when those source strings are empty. Preserve both sources and
> their evidence for high priority. All outputs remain drafts; send nothing.
> Use exactly one occurrence of each of ticket.classify,
> ticket.escalation_draft, ticket.normal_draft, branch.select_one,
> research.product, research.marketing, research.combine and research.write.
> The dependency list must contain exactly these nine edges between operation occurrences:
> ticket.classify -> ticket.escalation_draft; ticket.classify ->
> ticket.normal_draft; ticket.escalation_draft -> branch.select_one;
> ticket.normal_draft -> branch.select_one; branch.select_one ->
> research.product; branch.select_one -> research.marketing; research.product
> -> research.combine; research.marketing -> research.combine;
> research.combine -> research.write. Guard each of research.product,
> research.marketing, research.combine and research.write by comparing the
> unconditional ticket.classify step's priority to high. The research.write
> step must be the only terminal step. Step IDs and step list order are your
> choice. Do not add dependency edges, including redundant ones.

**Public fixture**

```json
{"ticket":{"id":"P-72","text":"Delivery is three days late.","days_overdue":3},"product_material":"The product supports barcode scanning and offline inventory.","marketing_material":"The service targets local shops through partner referrals."}
```

**Graph:** `classify → {escalate, normal} → select → {product, marketing} →
combine → write`. The eight operations are the four ticket operations followed
by the four research operations. Both joins are explicit `all_terminal`.
`product`, `marketing`, `combine`, and `write` each compare the unconditional
`classify.priority` to `high`. At combine use optional references to the two
conditional analyses; at write use an optional reference to conditional
combine; output uses an optional reference to write. The guard is evaluated
before resolving operation inputs. Do not guard on conditional product,
combine, or writer outputs, and do not pass null to an executed writer.

**Independent verifier:** high priority requires seven completed occurrences
and one skipped normal-action occurrence, four live LLM occurrences, source
fact/evidence checks, preserved analyses, and native ordering through both
joins. Normal priority requires three completed occurrences (classify, normal,
select), five skipped occurrences, one live LLM occurrence, and null brief.
All skipped operations must be absent from output data while their terminal
statuses propagate. Require no native Agency HTTP execution or accepted bridge
request for product, marketing, or writer on the normal path. A logical trace
alone cannot prove calls were skipped.

**Controls:** high fixture, normal at exactly two days with empty research
strings, and normal at zero with valid strings. In a separate changed fixture,
switch days 2→3 with valid sources: only the high route must produce a brief.
High with empty product/marketing is a deterministic runtime negative; normal
with the same empty strings must complete with null brief. Remove one guard:
normal input must expose an unintended operation/call or rejection. Drop a
skipped branch envelope: no accepted result. Reject a low-priority literal
brief, a high-priority null brief, a missing join input, a fabricated skip trace,
and corrupted source evidence. Check all four region guards, not only the two
analysis roots. Call budget controls must stop a faulty low-priority graph from
turning this negative control into paid research work.

## Isolation and independent acceptance

Create each business contract and its control fixtures before asking for a
generated solution. The verifier author owns arithmetic, boundary rules,
source-fact predicates, and adversarial mutations; it must not call production
operation functions to obtain expected results. Submitted step IDs can vary,
but the observable task obligations cannot be inferred solely from a graph
that may have omitted necessary work. Use a role/lineage contract with exact
allowed operation counts and expected input origins. Keep engine evidence and
business predicates at separate seams, as they are today.

Give the authoring model only the human task paragraph, public sample inputs,
common format/profile, and the same complete operation catalog. Do not include
reference YAML, solution scripts, verifier code, held-out fixtures, expected
answers, prior successful answers, or failed-attempt feedback. Reference YAML
may be prepared separately for oracle controls, but is not a generation fallback.
Once received, preserve the exact submission bytes; no fence removal, YAML
repair, reference substitution, or answer selection based on a live outcome.

The examples in this document and existing repository fixtures are public,
not hidden benchmark data. Fresh evaluation fixture values and marker strings
should be generated after the authoring package is frozen, stored with the
private run evidence, and supplied only to execution/verifier stages. Keep
oracle/verifier artifacts out of the generation package and its readable
workspace. The current wrapper's prompt prohibition and stderr marker scan do
not enforce that separation on the host. Use the linked experiment's audit and
isolation decision; if tools are not technically disabled, report an audited
cooperative pilot with residual access risk, not a protected held-out benchmark.

For each scenario retain a working reference control, a no-submission control,
deliberately incorrect outputs, and missing/corrupted native evidence. Execute
at least one deliberately broken exported workflow in n8n and require independent
acceptance to reject it despite successful engine execution. Label mutations of
copies of genuine records separately; those are verifier controls, not new
workflow executions. Never pass expected answers into the runtime interface.

## Sequence, budgets, and stopping rules

1. Finish the linked generated/live baseline gate for the existing families.
   Implement PREP-01 through PREP-04, freeze sources/prompts, and run unpaid
   controls. For a new topology, demonstrate it with prepared YAML and native
   n8n before charging for generation or live operations. Compiler success or a
   local JS result is insufficient.
2. For SC-01, then SC-02, then SC-03, then SC-04, implement only its canonical
   fixture contract and test package. Run reference/nop/mutation controls. Use
   two independent one-shot authoring attempts initially, with no repair
   feedback. Run both exact answers through the full stub fixture/control set.
   A failed answer remains in the denominator. Stop progression at the first
   failed gate, diagnose outside the run, and start a separately identified
   series after any change.
3. After both answers pass stub checks, promote the first submitted passing
   answer deterministically, not whichever performs best live. SC-01 requires
   no live operation calls; run its artifact through the selected live transport
   configuration to verify zero calls. For the other scenarios run only the
   bounded positive fixtures below, once each. Use deterministic endpoint
   responses and stubs for schema, corruption, timeout, and invalid-input
   controls. Do not replay malformed cases against the provider.
4. Freeze original YAML separately from per-case copies. Replace inputs and,
   when required, the execution deadline through the linked plan's declared
   overlay mechanism only. Record original/derived hashes and every allowed
   difference. Reject changed operations, graph, catalog, or acceptance rules.
   A 600-second per-case deadline is the initial upper bound for live cases;
   cap individual requests by remaining budget and inherit any stricter limit
   from the linked plan. Expired or ambiguous requests count as spent attempts.

| Initial expansion ceiling | Bound |
| --- | --- |
| Logical graph size | At most 9 steps, binary explicit joins, at most two terminal steps; this is an experiment admission bound, not a compiler capability claim |
| Fixture size | Each ledger at most 8 invoices; one ticket; at most 3 short articles; each source string at most 1,000 characters |
| Generation calls | 2 per scenario, at most 8 total; no automatic retry or repair |
| Runtime live cases | SC-01: 2 zero-call cases; SC-02: high/normal, 2 calls each; SC-03: 2 source pairs, 4 calls each; SC-04: high and normal-empty, 4 and 1 calls |
| Runtime Agency calls | At most 17 expected LLM occurrences across the selected positive cases; enforce per-case operation/occurrence admission and a series ceiling before dispatch |
| Total call accounting | At most 8 authoring wrapper attempts plus 17 runtime wrapper attempts for this expansion; count failures/unknown outcomes, distinguish HTTP node executions, bridge admissions, and provider calls |
| Monetary ceiling | Use the linked plan's enforceable spending policy. If reliable cost is unavailable, report call/time caps and unknown monetary cost; do not invent a currency budget from a token counter |

The 25 wrapper-attempt ceiling excludes the separately budgeted baseline and
requires no repeated hidden provider attempts. If the upstream wrapper can
retry internally without observable accounting, state that provider-call bound
as unverified and do not present 25 as a proven provider total. Stop immediately
on exhausted limits, unexpected calls, tool-audit violations, source hash drift,
missing native evidence, schema/acceptance failure, or unaccounted transport
outcomes. Preserve partial evidence. Do not increase caps or fix prompts inside
the same series to obtain a pass.

## Readiness for a bounded team pilot

After the initial expansion passes, commission one further one-shot authoring
attempt per scenario in a separately capped confirmation series, with fresh
private fixture values and frozen criteria. Three independent accepted attempts
per new task, plus one live artifact per LLM-bearing task evaluated on two
source/decision variants, is a reasonable entry gate for a small cooperative
pilot. It is a chosen operational gate, not a statistically established success
rate. Any failed attempt remains visible and blocks that gate until a new
versioned series is evaluated; never relabel retries as independent clean wins.

Readiness also requires all verifier corruption controls to reject, real n8n
evidence for the admitted topologies, zero unexpected live calls on skipped
paths, preserved input/source lineage, bounded execution records with no audit
gaps, and a human review of the live digest/research outputs for omitted or
invented facts. Record deterministic acceptance and human judgment separately.
The one-shot support packet may legitimately flag a rejected draft; the pilot
must display that rejection and must not publish it as an accepted reply.

Start with at most three team members and ten manually launched, synthetic-data
workflows restricted to these four task contracts, at most nine static steps,
the same catalog, and draft/preview outputs. Keep per-case limits at the table's
bounds and use a separately declared pilot series budget. Review every output
before any external use. No schedules, external side effects, private customer
data, new operations, dynamic collections, or mandatory parallelism are admitted
under this gate. A failure, manual YAML repair, required unsupported construct,
or unexpected call stops the pilot for diagnosis. Report it as evidence about
these four bounded compositions only, not general Sapiens applicability.
