# Two analyses and a report: YAML → n8n runtime trial

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a complete reference starting point;
copy it or improve its definition using the common profile and registered
operations. This task measures actual runtime behavior of a supplied YAML,
not autonomous Sapiens planning.

Analyze `product_material` and `marketing_material` independently. Each
analysis produces a nonempty summary and evidence from its own source.
Combine must wait for both completed analyses and preserve both unchanged.
Only then write a final object `{report, evidence}`. The report must cover
both analyses and the evidence array must retain the two corresponding
source excerpts in product, marketing order. Empty source material fails.

Use `research.product`, `research.marketing`, `research.combine`, and
`research.write`; `join: all_terminal` is required for fan-in. Independence
permits n8n to execute branches sequentially; do not claim simultaneous
progress. Keep the declared product, marketing and writer actors and their
allowed operation scopes. They are logical metadata, not separate Sapiens
processes. The verifier replaces both source strings with other material to
detect fixed or lost evidence.

LLM calls default to deterministic stubs. In that mode product and marketing
summaries are `Product: ` and `Marketing: ` followed by the full source;
the writer joins those summaries with two newlines and retains both complete
sources. A separately configured live trial permits different prose and
nonempty exact excerpts, while still checking data flow and preservation.
Automated live checks do not establish complete natural-language quality.
Write reports in English and retain the named capabilities, audience and
channels: the default case includes invoicing, stock tracking, API, small
shops, webinars and partner referrals. For alternate source material retain
its corresponding named capabilities, audience and channels. The verifier
checks factual terms with bounded alternatives, not exact generated prose.

Read `/app/lab/docs/PROFILE.md` and `/app/lab/src/sapi_config_lab/workflow/bindings.yaml`. Pin specification
revision `06ddd3333109cea8a2cb3071609070d7a3c0d3ff`; use Gantt, callback
fixtures, fail-fast errors and independent concurrency. The verifier imports
and executes generated JSON in pinned, isolated real n8n, checks both
analysis execution records and join ordering, then checks final data.
Modify only the submitted YAML. Do not change compiler/runtime/operations,
installed n8n or verifier. Do not connect a Sapiens harness or implement
refinement/digest lifecycle.
