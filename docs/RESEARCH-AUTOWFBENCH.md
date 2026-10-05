# AutoWFBench: source review and port feasibility

Inspected 2026-10-05; research only. No model calls, Docker/Harbor runs, benchmark
execution, provider provisioning, or upstream writes were performed. The local
Sapiens specification pin remains
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`.

## Revision and scope

The GitHub API resolved AutoWFBench main to
[`970bbc8645c4d503d35cb5df05363fb9de132519`](https://github.com/aleski-green/AutoWFBench/commit/970bbc8645c4d503d35cb5df05363fb9de132519),
authored 2026-10-04 20:45:10 UTC. Its recursive tree has 57 tracked files totaling
211,328 bytes. Pinned file contents, not the stale rendered default-branch README,
were used; this commit moved challenges into `benchmark/challenges/` and runtime
code into `autowfbench/runtime/`.
[Tree](https://github.com/aleski-green/AutoWFBench/tree/970bbc8645c4d503d35cb5df05363fb9de132519).

AutoWFBench is a small independent Python evaluation application: two synthetic
environments, an HTTP solution protocol, engine-owned evidence capture, a separate
Codex semantic judge, fixed score arithmetic, and a score/time dashboard. Each
attempt starts a fresh environment process. Candidate output is distinguished
from environment events and protected final checks. The engine freezes the
environment before judging and saves the package and run log; rescoring validates
their digests.
[Engine](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/engine.py#L32-L238).

It does not implement a Sapiens YAML compiler, Harbor integration, computer use,
or a finished n8n solution. Its executable sample is scripted Python behind the
solution API; n8n integration is documented as work for the solution author.
Descriptions of previously reviewed n8n workflows in feedback files concern
other submissions, not runnable n8n assets in this tree.
[Reference solution](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/interfaces/solution.py),
[adapter contract](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/solution-api.md#L1-L6),
[feedback context](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/feedback/production-checkout-recovery.md#L1-L19).

Python >=3.11 and `fastjsonschema>=2.21,<3` are the application dependencies.
A real judge additionally needs an authenticated Codex CLI and an explicit model;
the solution's own model is independent. No real CRM, email, payment service,
browser, or macOS Accessibility capability is needed. The application is intended
to run from a source checkout: challenge assets are not bundled in a standalone
wheel. No LICENSE/COPYING/NOTICE file occurs in the inspected tree and pyproject
has no license field; do not infer redistribution permission merely from public
availability. This does not block reading or designing an external adapter.
[Dependencies](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/pyproject.toml),
[operations](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/operations.md),
[synthetic scope](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/assignment/README.md#L69-L75).

## The two implemented cases

### CRM Lead Qualification

The workflow handles an inbound WhatsApp-support inquiry: collect missing facts,
consult service/policy and company evidence, qualify the existing lead, update
CRM, create a discovery follow-up, communicate to the customer, and verify the
result. Limits: 120 seconds and 40 tool calls. Tools are `inquiry.read`,
`documents.read`, `research.read`, `customer.ask`, `crm.read`,
`crm.update`, `followup.create`, and `customer.send`.
[Definition](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/crm-lead-qualification/definition.json).

The fixture is LEAD-1007, Crescent Retail Group: AED 180,000 budget, 12-week
timeline, 18,000 messages, Arabic/English, Salesforce, WhatsApp, human handoff.
Seed changes budget by `(seed % 5) * 10000` and volume by
`(seed % 7) * 1000`. Policy requires budget >=100,000 and timeline <=16 weeks,
with Salesforce subject to assessment. Thus all supplied seed variants remain
qualified; this is not evidence of rejecting unqualified leads. The first
attempt to set status Qualified fails with a retryable error.
[Fixture](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/crm-lead-qualification/environment.json),
[seed, tools and policy](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/environment.py#L73-L150).

Expected observable effects: exact qualification fields, Qualified status,
`next_action=discovery_call`, `owner=sales_coordinator`; exactly one follow-up
with `pending_scheduling`; clarification plus an authorized final message;
protected lead identity intact; failed update followed by successful update and
readback. Submit a final explanation; no named CRM artifact is mandatory.
The mock customer returns the whole fixed fact set after any valid nonempty
question list. Question relevance and unsupported promises are semantic matters,
not a realistic customer dialogue simulation.
[Final checks](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/environment.py#L181-L201),
[tool contract](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/solution-api.md#L75-L92).

### Production Checkout Recovery

The workflow reads an incident and source, observes failing tests, applies a
minimal fix, checks affected and previously working behavior, and submits
`incident-summary.md` plus a final answer. Limits: 120 seconds and 30 calls.
There are four tools: `incident.read`, `source.read`, `checkout.patch`,
`tests.run`. The source passes `currency, amount` to `charge_card` in the EUR
branch, while USD passes the correct argument order. Incident data includes
EUR-101 for 34.2 and a deployment note identifying the new EUR branch.
[Definition](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/production-checkout-recovery/definition.json),
[fixture](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/production-checkout-recovery/environment.json).

This is an AST interpreter accepting one checkout function, an optional EUR/USD
branch and restricted charge_card calls. It never executes arbitrary submitted
Python. Patches use an exact old fragment occurring once. Public tests use 34.2
and 15.05; protected checks use 1.01, 49.99 and `88 + seed % 17`, each in EUR
and USD, plus zero, negative, Boolean and unsupported-currency rejection.
The verifier checks fixed EUR behavior, regressions, a changed valid source with
no increase in line count, and fail-before-patch/pass-after-patch ordering.
This is a small controlled repair protocol, not a general production-code task.
[Interpreter](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/environment.py#L29-L70),
[tests and verifier](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/environment.py#L152-L201).

Two rubric limitations matter when interpreting results. “Minimal patch” is
approximated by grammar/changed source/line count, not semantic minimality.
Although the instruction requires `incident-summary.md`, the generic submission
schema permits an empty artifact list; there is no deterministic named-artifact
check. The semantic rubric can evaluate explanation quality but does not replace
an explicit filename gate.
[Submission schema](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/schemas/submission.schema.json),
[checkout scorecard](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/production-checkout-recovery/scorecard.json).

## What the numbers mean

| Component | CRM | Checkout |
| --- | --- | --- |
| Deterministic: 6 points | Lead fields 2; contact 1; follow-up 1; boundaries 1; recovery 1 | EUR result 3; regression 1; patch scope 1; tested recovery 1 |
| Semantic: 4 points | Evidence grounding 2; communication 1; honesty 1 | Evidence grounding 2; communication 1; honesty 1 |
| Total | Weighted score on 0–10 | Weighted score on 0–10 |

Each criterion is yes=1, maybe=0.33, no=0. The deterministic checks use only
yes/no. The judge fills only semantic criteria, cites existing event IDs of each
required evidence source, and cannot provide a numeric total. Python computes
`10 * sum(weight * answer) / sum(weights)` and rounds once to two decimals.
There is no numeric pass threshold. A separate `execution_pass` requires every
deterministic check plus normal completion.
[CRM scorecard](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/benchmark/challenges/crm-lead-qualification/scorecard.json),
[calculation and evidence validation](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/core/scoring.py#L9-L57).

Without a judge, total remains null and deterministic points remain available.
A failed/incomplete judge is not converted to zero. Demo mode assigns maybe to
every semantic item; a deterministic-perfect scripted reference therefore scores
7.32, which is not an LLM performance result. Real mode invokes a fresh
`codex exec` session and records prompt, raw response, events and provenance.
Implementation exists, but this review did not execute it; the committed test
of Codex invocation mocks the subprocess, so that test alone is not live-judge
evidence.
[Judge](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/runtime/judge.py#L16-L78),
[tests](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/tests/test_benchmark.py#L54-L129).

Deterministic behavior is seeded, but IDs/timestamps and durations vary; semantic
judging is stochastic. Comparisons must freeze task, environment, scorecard,
judge model/prompt and seed schedule. The specialist assignment asks for baseline
plus three improvements, three seeds and three attempts per seed: 36 attempts
per challenge, all retained. This is an assignment protocol, not proof that
those experiments have been completed and not authorization to run them here.
[Judging reproducibility](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/judging.md#L56-L63),
[assignment](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/assignment/README.md#L15-L35).

## Difference from sapi-config-lab

The lab tests a concrete experimental `sapi-lab/v0` YAML profile, its operation
catalog, compilation to native n8n, generated YAML authoring, runtime behavior,
and independent acceptance. AutoWFBench instead fixes a business environment and
evaluates any solution behind its HTTP API. The two occupy complementary layers:
our compiler/runtime can supply a candidate to their evaluator.
[Local architecture](ARCHITECTURE.md),
[local profile](PROFILE.md),
[upstream solution boundary](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/solution-api.md#L108-L117).

The local Harbor score is binary acceptance per submitted YAML/trial, not the
AutoWFBench weighted semantic score. Local Python criteria and native n8n
provenance determine acceptance; an LLM judge is not part of that local path.
Harbor stages/runs the tasks and collects verifier reward; it does not define
the business truth by itself. Oracle copies the reference YAML, nop supplies no
solution, and generation evaluates the returned one-shot YAML with references
removed. Authoring-time LLM calls and execution-time LLM calls remain separate.
[Local task assembly and verifier design](ARCHITECTURE.md#why-reports-still-contain-copies),
[local reporting contract](REPORTS.md).

The concrete local path initializes `/logs/verifier/reward.txt` to 0 and changes
it to 1 only after the independent verifier exits successfully. All required
case rows must pass, including expected-rejection and corruption controls;
96 passing rows do not mean 96 independent tasks. Reference YAML is an oracle
input, not the expected text against which generated YAML is compared.
[Reward script](../harbor/templates/test.sh#L4-L6),
[all-rows decision](../verification/verify.py#L354-L355),
[task assembly](../src/sapi_config_lab/experiments/tasks.py#L62-L92),
[business criteria](../verification/business.py#L50-L161).

A saved authoring series has nine completed, nonerrored trials (three attempts
each for invoice/routing/research), nine accepted submissions, and Harbor mean
reward 1.0. Runtime LLMs were stubbed. This means 9/9 observed trial passes,
not a population success probability or live runtime-model quality. Its 96
diagnostic rows comprise 27 positive cases, 33 invalid runtime cases, nine
corrupted-workflow controls and 27 invalid definitions. A separate control series
records oracle 3/3 and nop 0/3. These are existing artifacts, not runs performed
for this review.
[Harbor result](../reports/20261004-yaml-generation-1/jobs/generated/result.json),
[authoring report](../reports/20261004-yaml-generation-1/report.json),
[controls](../reports/20261004-scenario-expansion-integration/baseline-control/report.json).

Do not invent a fractional local score by dividing passed rows by observed rows:
the verifier can stop on the first failing case. A future rubric must freeze
the full planned denominator and distinguish not-run from failure. Also retain
historical failed series: the original SC-03 0/1 and separately corrected 2/2
are different conditions, not a silently cleaned-up success rate.
[Verifier exception path](../verification/verify.py),
[original SC-03](../reports/20261004-scenario-expansion-integration/sc03-generation/report.json),
[corrected SC-03](../reports/20261004-corrected-expansion-integration/sc03-generation/report.json).

## Concrete transfer plan

Two integration routes are viable; neither has been implemented here:

| Route | What remains unchanged | Work required |
| --- | --- | --- |
| Upstream runner plus our solution | AutoWFBench orchestration, environment, checks, judge and score | Add the HTTP solution adapter and local tool transport/catalog below. This is the smaller first protocol-parity proof; it does not require moving existing lab tasks away from Harbor. |
| Same case inside Harbor | Original fixtures, simulator, independent verifier, scorecard and judge | Package the trusted upstream evaluator and a candidate adapter as an isolated Harbor task; retain upstream evidence and map only a completed score into Harbor reward. Then evaluate local YAML/n8n candidates behind that adapter. |

Harbor permits a numeric `reward.txt` or multiple numeric metrics in
`reward.json` (the latter takes precedence when both exist). A future wrapper
can emit completed AutoWFBench `score_0_10 / 10` as its primary normalized reward
and preserve raw score, deterministic points and execution_pass as separate
artifacts/metrics. This differs from the lab's present binary reward. Missing or
failed judging must remain explicitly unscored/evaluation-failed, with no invented
zero total or automatic pass. If Harbor's aggregate treats missing rewards as
zero, report the scored/unscored denominators separately; that aggregate is not
the mean of upstream scored runs. Fix one reward contract before comparing runs.
[Harbor task reward contract](https://www.harborframework.com/docs/tasks),
[upstream score statuses](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/autowfbench/core/scoring.py#L32-L57).

For an initial parity proof, use known-good and incomplete scripted candidates
against the untouched evaluator through either route before adding generated
YAML or live models. Recreating merely similar cases with local binary checks
would be a new benchmark condition, not the same AutoWFBench score.

1. Freeze the observed upstream commit and challenge files separately from the
   unchanged Sapiens pin. Keep evaluator storage, admin/finalize API and judge
   credentials outside the candidate. Use the source checkout without copying
   upstream source into a distributed package until its reuse terms are clear.
2. Implement the solution adapter: asynchronous `POST /runs` (acknowledge within
   15 seconds), `GET /runs/{execution_id}`, and `POST /runs/{execution_id}/cancel`.
   Carry run ID, environment URL/token and limits into each isolated candidate.
   Return the exact submission envelope, final answer, artifacts and candidate
   trace. Environment events must still come from the environment.
3. Add catalog contracts and actual tool transport for the upstream capabilities.
   Each business action calls `POST {environment.base_url}/tools`; do not replace
   it with fixture-returning Script code or a Script that solves the whole case.
   The existing LLM transport is not automatically this business-tool API.
4. Author a YAML composition, supply required patch/report or CRM decisions using
   explicit model operation contracts, and retain native n8n evidence. Keep
   upstream protected state checks independent of those model outputs.
5. Establish deterministic protocol controls first: known good scripted candidate,
   unchanged/incomplete candidate, wrong fields/patch, missing required effects,
   timeout, and an error response. Add the real judge only in an explicitly
   budgeted later experiment; preserve unscored runs and failures.
6. For Harbor authoring trials, stage only the public task and tool/catalog
   contract, freeze the returned YAML, and replay that YAML through the unchanged
   AutoWFBench solution interface. Preserve separate fields for local authoring
   acceptance, upstream execution_pass, upstream 0–10 score, model calls and
   timing. Do not collapse them into one “success” field.

These steps are a proposed integration, not implemented functionality.
The public API already documents Docker host routing and separation between run
and administrative tokens. A loopback host environment URL will not work
unchanged from inside an n8n container.
[API and n8n wiring](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/solution-api.md),
[network and deployment](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/operations.md#L16-L35).

Checkout is the smaller first case: four tools and a straightforward
read → failing tests → patch → passing tests → report graph. It still needs
new real tool bindings/transport and patch/report model contracts; it is not
just new YAML over today's catalog. CRM adds eight tools and explicit recovery.
The upstream returns tool failures as HTTP 200 with `ok=false`; a bounded
conditional second update can be modeled as data-level recovery if its error
envelope is exposed to YAML. General transport retry, backoff, open-ended
agent loops and durable human waits would require additional semantics, and are
not necessary to reproduce this fixed simulator. Its customer reply is immediate.
[Error envelope and tool list](https://github.com/aleski-green/AutoWFBench/blob/970bbc8645c4d503d35cb5df05363fb9de132519/development/docs/reference/solution-api.md#L62-L106),
[local conditions, errors and extension costs](PROFILE.md).

The present catalog contains invoice, ticket/routing, research, reply and digest
operations; it has no CRM, checkout-patch or generic sandbox tool. New business
operations require contract, implementation and binding. The Agency HTTP
transport currently carries model-operation requests, not arbitrary external
tool calls. DAG guards and explicit returned error objects can represent this
bounded fixture's conditional retry without claiming that general durable retry
already exists. Bounded refinement repeats a whole graph region; it must not
blindly replay CRM side effects or create duplicate follow-ups. The typed
WorkflowBackend seam does not require a new backend for this integration:
n8n can stay the selected executor.
[Catalog](../src/sapi_config_lab/workflow/bindings.yaml),
[profile limits](PROFILE.md#validator-limits),
[backend interface](../src/sapi_config_lab/runtime/contracts.py#L77-L92).

No alternate executor, Sapiens harness, desktop automation or real external
business service is required for either synthetic case. A later Sapiens authoring
or execution experiment remains a separate question; the existing feasibility
note distinguishes those roles and does not establish a Sapiens YAML executor.
[Prior pinned-source feasibility](planning/sapiens-harness-feasibility.md).
