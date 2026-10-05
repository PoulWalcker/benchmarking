# Frozen records

These files are records of what happened on specific dates. They are **not
current instructions**. Their commands, paths, counts and status statements
describe the checkout as it was when each was written, and several are
explicitly superseded by a reference document at the top of `docs/`.

Nothing here is edited to match the current tree. The bytes of each file are
preserved exactly as they were before this directory existed. If a command below
differs from the one in a current reference document, the reference document is
correct.

| Record | What it records | When |
| --- | --- | --- |
| [ANALYSIS.md](ANALYSIS.md) | The original expressiveness/compilation/validation analysis written for the prototype, kept as prototype history; its own banner says its references describe the prototype and not this checkout | Original analysis, banner dated October 4, 2026 |
| [BUSINESS-EVALUATION-STATUS.md](BUSINESS-EVALUATION-STATUS.md) | Measured status of the two-task pilot: the eight-area implemented/remaining table, dispatch counts, and the two CRM authoring attempts that returned HTTP 500 and were never scored | 2026-10-05 |
| [PROTOTYPE-README.md](PROTOTYPE-README.md) | The README from before the 2026-10-04 refactoring. Its commands reference `lab.py`, `scripts/` and `bridge/`, which no longer exist, and it states Python 3.10 while `pyproject.toml` requires `>=3.14,<3.15`. Superseded by the repository `README.md` | Before 2026-10-04 |
| [RESEARCH-AUTOWFBENCH.md](RESEARCH-AUTOWFBENCH.md) | A one-time source review of upstream AutoWFBench at its pinned commit and an assessment of porting it; research only, no runs | Inspected 2026-10-05 |
| [RESULTS.md](RESULTS.md) | Curated outcomes and SHA-256 identifiers of the preserved local report directories from the October 2026 series | October 4, 2026 |
| [generated-yaml-live-execution.md](generated-yaml-live-execution.md) | The preparation plan for the frozen generated-YAML/live replay, with its completion status update. Superseded as a run guide by [GENERATED-LIVE.md](../GENERATED-LIVE.md) | Plan, status update October 4, 2026 |
| [sapiens-harness-feasibility.md](sapiens-harness-feasibility.md) | A point-in-time feasibility inspection of replacing n8n with a Sapiens executor, with upstream revisions resolved on the inspection date | Inspected 2026-10-04 |
| [scenario-expansion.md](scenario-expansion.md) | The preparation backlog for the four-scenario expansion, with its completion status update. Superseded as a run guide by [SCENARIO-EXPANSION.md](../SCENARIO-EXPANSION.md) | Backlog, status update October 4, 2026 |

## Reading the links inside these files

The bytes are frozen, so the relative links written inside these records still
point at the paths the records had before they were moved here. **Those links do
not resolve from this directory.** Four rules translate them:

- `NAME.md` with no directory means `docs/NAME.md`, the parent of this
  directory — unless the named file is one of the records listed above, in which
  case it is right here beside you.
- `../NAME.md` also means `docs/NAME.md`, for the three records that were
  previously under `docs/planning/`.
- `../README.md` means the repository `README.md`, two levels up.
- `../src/...`, `../verification/...`, `../harbor/...`, `../provenance/...` and
  `../generation/...` mean those directories at the repository root, two levels
  up rather than one.
- `planning/sapiens-harness-feasibility.md` means
  [sapiens-harness-feasibility.md](sapiens-harness-feasibility.md) here.

Links into `reports/` were already dead before the move: that tree is listed in
`.gitignore` and is never committed or distributed. The artefacts those links
cited have since been extracted into the committed [`evidence/`](../../evidence/README.md)
tree and the links inside these records were repointed at it, so they now
resolve; the full run directories are archived outside the repository.
`RESULTS.md` still records the identifying hashes of the original directories.
