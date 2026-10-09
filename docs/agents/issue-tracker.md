# Issue tracker: Local Markdown

Issues and specs live under `.scratch/`, with one directory per feature.

## Conventions

- Spec: `.scratch/<feature-slug>/spec.md`.
- Tickets: `.scratch/<feature-slug>/issues/<NN>-<slug>.md`,
  numbered from `01`, one file per ticket.
- Triage role: a `Status:` line near the top, using the mapping
  in `triage-labels.md`.
- Comments: append under `## Comments`.

When a skill says to publish to the issue tracker, create the
corresponding local file. When it says to fetch a ticket, read
the referenced file; resolve ticket numbers within their feature.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`, containing Notes,
  Decisions-so-far and Fog.
- Child ticket: `issues/NN-<slug>.md` within that effort.
  Record `Type: research`, `prototype`, `grilling` or `task`.
- Track work separately from triage with `Progress: open`,
  `claimed` or `resolved`. New tickets start open.
- Blocking: `Blocked by: NN, NN` refers to tickets in the same
  effort. A ticket is unblocked when every blocker is resolved.
- Frontier: choose the first open, unblocked ticket by number.
- Claim: save `Progress: claimed` before starting work.
- Resolve: append the answer under `## Answer`, set
  `Progress: resolved`, and add a gist and relative ticket link
  to the map's Decisions-so-far.
