# bulletin-market-brief runtime control

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a prepared control; copy or improve
it using the common profile and registered operations. This package tests
YAML execution and independent acceptance.

Create a Gantt workflow with logical id bulletin-market-brief. Prepare the supplied product bulletins, summarize them, and retain the digest as a preview with its article IDs. Analyze product capabilities from that digest's actual text. Independently analyze the supplied marketing material, combine those analyses, and write a short source-grounded brief. Return exactly digest and brief. Preserve every named capability, audience, and channel in the supplied small fixture; retain evidence through the analyses, join, and final brief. Product evidence must quote the digest text actually passed to its analysis. Do not fetch outside material, publish, schedule, or invent product claims. Use the registered digest and research operations. Assign digest summarization, product analysis, marketing analysis, and writing to four separate logical actors, each allowed only its own model operation; actor names are your choice.

Use exactly one occurrence of each of digest.prepare, digest.summarize, digest.preview,
research.product, research.marketing, research.combine and research.write. The
dependency list must contain exactly these six edges between operation occurrences: digest.prepare ->
digest.summarize; digest.summarize -> digest.preview; digest.summarize ->
research.product; research.product -> research.combine; research.marketing ->
research.combine; research.combine -> research.write. The research.product material
input must reference the text field directly from the digest.summarize step. The
digest.preview and research.write steps must be the only two terminal steps; preview has
no outgoing dependencies. Step IDs and step list order are your choice. Do not add
dependency edges, including redundant ones.

Keep Callback fixture activation, revision 1, independent concurrency and
fail-fast errors. The common profile and complete operation catalog are in
`/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/core/bindings.yaml`.
Only modify the submitted YAML. Do not alter the compiler, runtime, installed
n8n or verifier. The verifier replaces sample inputs and checks real n8n
records plus independent business obligations. All outputs remain local
drafts or previews; no external side effects are permitted.
