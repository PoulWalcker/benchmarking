# Task-specific lifecycle extension

This document overrides FORMAT's static-profile prohibition on `lifecycle` and Cron **only for daily-digest**. The candidate graph still uses the same registered operations, dependency/reference rules and pinned specification. The complete run consists of the durable lifecycle controller and its native candidate n8n graphs; importing one candidate JSON alone does not implement the controller.

Use these exact policy fields. They describe the execution contract, not a candidate step graph:

```yaml
activation:
  kind: Cron
  rule_id: daily-digest-0900
  schedule: "0 9 * * *"
  timezone: Asia/Dubai
  enabled: false
  workflow_ref: {id: daily-digest, revision: 1}
execution:
  concurrency: independent
  deadline_seconds: 120
  on_step_error: fail
lifecycle:
  initial_state: draft
  test:
    initiator: Callback
    rule_id: digest-test-requested
    hook: digest.test_requested
    condition: test_environment_ready
    reaction: Trigger
    verifier: digest.acceptance_v1
    output_mode: preview
  on_test_pass:
    action: release_candidate
    enable_cron: daily-digest-0900
    retarget: exact_tested_revision
  on_test_fail:
    action: wbs_rebuild
    objective: Repair the candidate using verifier findings.
    source_work: current_candidate
    output: new_workflow_fork
    max_rebuilds: 2
    archive_original: after_fork_persisted
    next: request_test_via_callback
    exhausted: suspend_and_create_adhoc
  persistence:
    store: workflow_registry
    immutable_definitions: true
    record_test_results: true
    deduplicate_by: [rule_id, event_id]
  scheduling:
    overlap: skip_if_running
    missed_run: skip
    suspended: do_not_start
  replacement:
    in_flight: finish_pinned_revision
    activate_new_revision: after_previous_run_finishes
    restore_archive: restore_definition_without_replay
```

The verifier checks native business output independently. `digest.acceptance_v1` is a registered verifier identifier, not a catalog operation. Callback and Cron initiate complete candidate runs; they are not graph nodes. A test schedule overlay belongs to the evaluation controller and must not replace the submitted daily schedule.
