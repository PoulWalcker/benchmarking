# Extracted evidence

This tree exists so that the evidence links in `README.md` and `docs/**`
resolve from a clean clone. It is committed; the local `reports/` tree that
originally held these files is listed in `.gitignore`, was never committed and
was never distributed, so every one of those links was dead for anyone who did
not have the author's working copy.

## These are extracts, not runs

Each file here is a byte-for-byte copy of one artefact from a local run
directory, placed at the same path it had under `reports/`. So
`reports/20261004-verified-2/live/report.json` is `evidence/20261004-verified-2/live/report.json`.

**Only the artefacts the documentation actually cites were extracted**, plus the
small artefacts those cited files themselves link to. The run directories they
came from are not here and are not reconstructible from this tree.

What was dropped:

- `jobs/` trees — per-case n8n execution payloads, container logs, prepared
  workflow JSON and verifier working directories. These are the bulk of every
  run; several of them embed a full copy of the source tree as it stood when the
  run executed.
- Task packages under `task-packages/`, Harbor images and build context.
- Intermediate and superseded runs that nothing cites: the
  `expansion-author-freeze` series, `verified`, the `refactor-baseline` /
  `-runtime` / `-final` sequence, the `checkout-controls-*` and `crm-controls-*`
  families, and the other October 2026 runs.
- Every artefact of a cited run that no document links to.

Because whole directories were dropped, a link written *inside* one of these
extracted Markdown files may point at a file that was not extracted. The links
reached from `README.md` and `docs/**` all resolve; deeper ones may not.

## Where the full runs live

The complete, uncompressed run directories were archived out of the repository,
one compressed archive per run, to:

    /Users/pashvel/Desktop/sapi-lab-archive/

That path is on the author's machine and is not part of this repository or any
distribution. `docs/history/RESULTS.md` records the SHA-256 identifiers of the
preserved report files, so an artefact here can be checked against the record
without the archive.

## What is here

| Run | Files extracted |
| --- | --- |
| `20261004-corrected-expansion-integration/` | 20 |
| `20261004-generated-live-integration/` | 1 |
| `20261004-imported-workflow-diagnosis/` | 1 |
| `20261004-open-tasks-integration/` | 39 |
| `20261004-scenario-expansion-integration/` | 22 |
| `20261004-verified-2/` | 1 |
| `20261004-yaml-generation-1/` | 2 |

86 files in total, 1.6 MB. New runs still write to `reports/`, which stays
ignored; nothing writes here. A file is added to this tree only when a document
starts citing it.

One of them is not cited by a document: `20261004-open-tasks-integration/wrapper-identity.json`
is the inspected Codex-wrapper identity record that `sapi-lab ui open --live`
reads before it will contact the model. The UI state directory (`var/ui/`, which
is ignored) points at it by absolute path, so it has to stay on disk.
