# Four bounded compositions

The four prepared tasks reuse the unchanged `sapi-lab/v0` profile, complete
operation catalog and specification revision
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`. Their implementation is separate from
execution evidence. A local JS probe is not a native n8n run, and passing stubs
does not establish live model quality.

| Ordered scenario | Steps | Selected live cases | Maximum runtime attempts |
| --- | ---: | --- | ---: |
| `dual-ledger-closeout` | 6 | base-ledgers, alternate-ledgers | 0 |
| `support-review-packet` | 9 | high-packet, normal-packet | 4 |
| `bulletin-market-brief` | 7 | base-bulletins, alternate-bulletins | 8 |
| `priority-support-brief` | 8 | high-brief, normal-empty | 5 |

Each graph has only binary explicit joins and at most two terminal steps. The
packet's single draft may fail its review: `review.pass=false` is an honest
successful packet outcome. The bulletin uses its actual digest as the product
analysis source. Normal priority skips all four research steps and returns a
null brief, including when research source strings are empty.

## Run the admitted sequence

By default, use one new series directory for all four scenarios, in table order. Existing
commands without scenario selection still use the original three tasks. These
commands launch real controls and, after their gates pass, paid model calls:

```bash
SERIES=reports/my-expansion-series
SCENARIO=dual-ledger-closeout
RUN=reports/my-expansion-dual-ledger

uv run --locked python -m sapi_config_lab.experiments.generation.run \
  --scenario "$SCENARIO" --attempts 2 --series-dir "$SERIES" --report-dir "$RUN-generation"

uv run --locked python -m sapi_config_lab.experiments.replay \
  --scenario "$SCENARIO" --source-report "$RUN-generation/report.json" \
  --output "$RUN-selection.json"

uv run --locked python -m sapi_config_lab.experiments.live \
  --scenario "$SCENARIO" --series-dir "$SERIES" \
  --stub-report "$RUN-generation/control/report.json" \
  --submissions-manifest "$RUN-selection.json" \
  --wrapper-evidence /absolute/path/to/private-wrapper-identity.json \
  --report-dir "$RUN-live"
```

Choose new `SCENARIO` and `RUN` values for the next row only after the preceding
live report passes. Keep the same `SERIES`. `--preflight-only` on the live command
checks the frozen selection without dispatching. For unpaid prepared controls
alone, use `uv run --locked sapi-lab harbor --scenario NAME --report-dir NEW`.
The [generated/live guide](GENERATED-LIVE.md) describes wrapper inspection and
the cooperative tool-access limitation.

For a fresh subset, add the same repeated `--series-scenario` arguments to every
generation and live command. The subset must follow the table's order. For the
corrected SC-03/SC-04 evaluation, use:

```bash
--series-scenario bulletin-market-brief --series-scenario priority-support-brief
```

Start with `--scenario bulletin-market-brief`, then use
`--scenario priority-support-brief` after its predecessor's final live report
passes. This selection freezes ceilings of four authoring and thirteen runtime
attempts. It creates no SC-01/SC-02 events and admits neither task. Omitting the
subset arguments selects all four scenarios, so keep the same explicit subset
on every command that uses its ledger. The replay selection command is unchanged.

Fresh `sapi-lab-expansion-series/v2` ledgers freeze the ordered scenario list,
derived ceilings and source inventory. An existing ledger's selection cannot
change, and v1 ledgers are not migrated. Keep stopped runs intact and choose a
new series directory for a separately authorized evaluation.

Generation freezes the complete public prompts before creating fresh private
fixture IDs, amounts and source markers. The fixed lexical predicates, negative
rules and decision boundaries remain unchanged. Both one-shot answers use that
same private corpus; the prompt contains only the public sample. The generated
report and selection pin the exact private `tests/cases.json` bytes. Live replay
uses those bytes and the first submitted answer, after both generated stub gates
pass. Original YAML bytes remain unchanged; runtime overlays replace only inputs
and the declared execution deadline.

The durable `series.json` reserves attempts before launch, admits exactly two
ordered authoring attempts per task and the named runtime cases, and enforces
derived ceilings: eight authoring plus seventeen runtime wrapper attempts for
the default full selection, or the selected tasks' fixed totals. Failure or
an unknown outcome blocks later work in that series. It cannot be resumed by
rerunning a command or increasing an editable cap. Each case also has an exact
operation/step admission grant, a 600-second deadline and requests capped at
185 seconds or remaining time. Zero-call and normal-priority cases cannot admit
extra research calls. Provider-internal requests and currency cost remain
unknown; the wrapper must expose no internal retry.

## Acceptance and limits

Independent role contracts bind submitted step IDs by operation, input origin,
guard, dependencies and output lineage, allowing renamed IDs and any list order.
SC-03 and SC-04 are authoring tasks with a prescribed topology. Their public
prompts state the exact permitted dependency edges, source references and
terminal roles. Acceptance checks those obligations; it does not evaluate every
workflow that produces equivalent business results. The native checker verifies
each occurrence and edge. Business checks use Python
integer totals, the `> 2` routing rule, UTF-16 reply length, actual source excerpts,
and frozen lexical fact coverage. Negative controls cover malformed inputs,
corrupted values/evidence, missing native records, occurrence swaps, guard
omissions, and a deliberately broken exported workflow for every task.

Live text still requires separate human review for invented or omitted facts.
Reports retain `human_review: pending` where applicable. This initial two-attempt
series does not establish the later confirmation gate or team-pilot readiness;
a third attempt per task and human review require separate evidence. Historical
reports, existing services, the extension drafts and upstream specification stay
outside this expansion's changes.
