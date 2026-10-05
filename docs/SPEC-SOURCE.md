# Pinned specification source

`provenance/spec-source.json` identifies the public upstream repository, exact commit,
upstream path, local snapshot, and SHA-256. The supported snapshot remains pinned to
`06ddd3333109cea8a2cb3071609070d7a3c0d3ff`; no external checkout path is needed.

Verify the recorded snapshot without network access:

```sh
.venv/bin/python infra/verify_spec_source.py verify
```

Explicitly fetch a candidate revision into a new ignored directory and show its diff:

```sh
.venv/bin/python infra/verify_spec_source.py compare --revision main --output-dir reports/spec-comparison-new
cat reports/spec-comparison-new/changes.diff
```

The command resolves the requested ref once, downloads files at that immutable commit,
and saves candidate bytes, hashes, and a unified diff. It never edits the pinned manifest
or snapshot. Review the saved diff and compatibility impact before a separately approved
pin update; a differing upstream file is evidence to review, not automatic upgrade authority.
Use the pinned commit as `--revision` to verify its upstream correspondence.

The vendored snapshot keeps builds and verification offline and avoids a submodule checkout
requirement. Updates are explicit; maintaining this small manifest and reviewing upstream
diffs is the corresponding maintenance cost. Local comparison reports remain excluded from
Git and distribution archives; the manifest and snapshot are included in the source archive.
