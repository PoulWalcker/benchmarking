# dual-ledger-closeout runtime control

Put a valid `sapi-lab/v0` YAML definition at `/app/submission/config.yaml`.
The supplied `/app/scenario/base.yaml` is a prepared control; copy or improve
it using the common profile and registered operations. This package tests
YAML execution and independent acceptance.

Create a Pipeline with logical id dual-ledger-closeout. Close the domestic and export invoice ledgers independently. Validate each ledger, sum its validated amounts, and create its own report. Return exactly domestic and export, each containing total_minor, currency, and invoice_count. Never mix the ledgers or convert currencies. Either invalid ledger must fail the whole run without a successful result. Use the registered invoice operations for both ledgers and derive both reports from the supplied values. The input fields are domestic_invoices and export_invoices. No model call is needed.

Keep Callback fixture activation, revision 1, independent concurrency and
fail-fast errors. The common profile and complete operation catalog are in
`/app/lab/docs/PROFILE.md` and
`/app/lab/src/sapi_config_lab/workflow/bindings.yaml`.
Only modify the submitted YAML. Do not alter the compiler, runtime, installed
n8n or verifier. The verifier replaces sample inputs and checks real n8n
records plus independent business obligations. All outputs remain local
drafts or previews; no external side effects are permitted.
