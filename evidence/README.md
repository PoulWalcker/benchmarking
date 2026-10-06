# Evidence

Committed, byte-for-byte extracts of local runs that durable claims cite. Each file keeps the path it had under `reports/`, so `reports/<run>/live/report.json` is `evidence/<run>/live/report.json`.

- Extracts are immutable: never edit, reformat or regenerate them.
- Only cited artifacts were extracted; links inside an extract may point at files that were not.
- Full run directories are archived outside the repository; recorded report hashes identify them.
- A file is added here only when a claim starts citing it. New runs write to `reports/`.

Evidence shows what a recorded run did with the code and configuration of that time, not what the current tree does.
