# Bounded reply refinement evaluation

Status: all admitted automatic gates passed. Independent raw-evidence review is recorded separately.

- Three independent one-shot YAML authoring calls, all accepted after real native stub controls; zero repairs or transport retries.
- Chronologically first original selected: `revise-answer__potobY3`, SHA-256 `15ea95f0e12b7b3cd2df541a8e59619087d1d2bd4a92028e286a99278afaa29b`. Its bytes are preserved and copied unchanged into verification.
- Actual live positive case accepted attempt 1 and emitted the checked reply. No later attempt executed.
- Actual impossible-limit case executed all three internal attempts, consuming actual prior draft and review feedback; n8n failed at Result with no accepted output. This is the required negative outcome, not a successful reply.
- Four outgoing runtime wrapper attempts and four completions matched four native Agency requests; cap was six. There are no unrun cases. Provider-internal request counts and cost remain unknown.
- Native live tests use explicit fixture inputs and a 600-second deadline overlay. No model-authored graph repair occurred.

The archived source manifest is `../refinement-fixed-freeze/source-manifest.json` (132 files, SHA-256 `7ccb4dbca7748b874cf9fd633cff69c915e0f1619b294e70964661ea4abdf3e6`). The original failed unpaid gate in `../refinement-evaluation/` remains immutable: it exposed a verifier node-presence assumption, corrected before any model call. The corrected prompt remained `94a2fa3749b2c3be6355247ae36d7759f71db995c9e4b54212d1c2e4dd6f5e96`.

Evidence: [generation](generation/report.json), [selection](selection.json), [live](live/report.json), [correlation](live/correlation.json), [durable series](series/series.json).

The positive live reply did not require revision. Actual rejection→correction→acceptance was observed in deterministic native controls; the live negative case proves feedback carry and bounded exhaustion. This finite task/catalog experiment does not prove general workflow authoring or production readiness.
