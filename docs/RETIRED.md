# Retired experiment runners

These experiments finished. Their recorded evidence stays under `evidence/`
and their write-ups stay where they were; only the code that ran them was
removed from the current tree. To rerun one exactly, check out the last
revision that contained it:

```bash
git worktree add ../sapi-retired a9e2eb14ef1c641be63617464c9d29bd5a913d34
cd ../sapi-retired && uv sync --locked --extra harbor
```

| Experiment | Removed code | Write-up | Last revision |
| --- | --- | --- | --- |
| Two-task composition (generalization) | `interfaces/generalization.py`, `verification/generalization.py`, `verification/generalization_submission.py`, `generation/generalization/` | [GENERALIZATION-EVALUATION.md](GENERALIZATION-EVALUATION.md) | `a9e2eb1` |
| Daily-digest lifecycle series (1+2+6 admission) | `interfaces/lifecycle_run.py` | [LIFECYCLE-EVALUATION.md](LIFECYCLE-EVALUATION.md) | `a9e2eb1` |
| `sapi-lab checkout` alias | the alias only; use `sapi-lab benchmark` | — | `a9e2eb1` |

Still supported and not affected: the lifecycle controller (`sapi-lab lifecycle`),
the daily-digest control verifier (`verification/lifecycle_submission.py`), and
the daily-digest generation package (`sapi-lab package-tasks --mode generation
--scenario daily-digest`). `sapi-lab generate` refuses daily-digest, as before,
because its authoring needed the retired series runner.

The commands printed in those write-ups name modules that no longer exist in
this tree. They are historical records and are not edited.
