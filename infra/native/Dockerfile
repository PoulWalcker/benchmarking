# Only these positive public inputs enter candidate image ancestry.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS public
RUN apk add --no-cache python3=3.14.8-r0 py3-yaml=6.0.3-r2 bash=5.3.9-r1 && adduser -D -u 1000 author && mkdir -p /app/submission /submission && chown author /app/submission && chmod 700 /submission
COPY --from=sapi-core harbor_integration/submission.py /opt/sapi-submission.py
COPY generation/PROFILE.md generation/FORMAT.md /app/public/
WORKDIR /app
USER 1000
CMD ["sleep", "infinity"]

FROM public AS invoice-total-public
COPY tasks/invoice-total/instruction.md tasks/invoice-total/task.md tasks/invoice-total/bindings.yaml /app/public/

FROM public AS research-report-public
COPY tasks/research-report/instruction.md tasks/research-report/task.md tasks/research-report/bindings.yaml /app/public/

FROM public AS checkout-recovery-public
COPY tasks/checkout-recovery/instruction.md tasks/checkout-recovery/authoring-notes.md tasks/checkout-recovery/bindings.yaml tasks/checkout-recovery/authoring-prompt.txt /app/public/

# Trusted stages use the existing pinned n8n runtime, never a public descendant.
FROM sapi-native-runtime:phase1 AS core
COPY --from=sapi-identity source-manifest.json /opt/source-manifest.json
COPY --from=sapi-core __init__.py contracts.py evidence.py paths.py profile.py pinned_source.py net.py wrapper_audit.py /tests/core/sapi_config_lab/
COPY --from=sapi-core compile/__init__.py compile/n8n.py compile/refinement.py compile/runtime-fragment.js /tests/core/sapi_config_lab/compile/
COPY --from=sapi-core execute/__init__.py execute/n8n.py execute/agency.py execute/host.py execute/agency-prompt.md /tests/core/sapi_config_lab/execute/
COPY --from=sapi-core evaluate/__init__.py evaluate/records.py /tests/core/sapi_config_lab/evaluate/
COPY --from=sapi-core coordinate/__init__.py coordinate/backend.py coordinate/cases.py coordinate/observe.py coordinate/task_worker.py coordinate/native_record.py /tests/core/sapi_config_lab/coordinate/
COPY --from=sapi-verification *.py /tests/core/verification/
COPY --from=sapi-core harbor_integration/submission.py /opt/sapi-submission.py
ENV PYTHONPATH=/tests/core:/tests PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
RUN mkdir -p /submission /logs/artifacts

FROM core AS invoice-total-verifier
COPY tasks/invoice-total/experiment.py /tests/payload/experiment.py
COPY tasks/invoice-total/bindings.yaml tasks/invoice-total/operations.js tasks/invoice-total/cases.json /tests/payload/
COPY tasks/invoice-total/evaluation /tests/payload/evaluation
RUN python3 -c "from payload.evaluation.evaluator import plan, evaluate; from sapi_config_lab.coordinate.task_worker import run_task"

FROM core AS research-report-verifier
COPY tasks/research-report/experiment.py /tests/payload/experiment.py
COPY tasks/research-report/bindings.yaml tasks/research-report/operations.js tasks/research-report/cases.json /tests/payload/
COPY tasks/research-report/evaluation /tests/payload/evaluation
RUN python3 -c "from payload.evaluation.evaluator import plan, evaluate; from sapi_config_lab.coordinate.task_worker import run_task"

FROM core AS checkout-recovery-verifier
COPY tasks/checkout-recovery/experiment.py /tests/payload/experiment.py
COPY tasks/checkout-recovery/tests/calibration/judge-reply.json /tests/calibration/judge-reply.json
COPY tasks/checkout-recovery/bindings.yaml tasks/checkout-recovery/operations.js /tests/payload/
COPY tasks/checkout-recovery/evaluation /tests/payload/evaluation
COPY tasks/checkout-recovery/environment/__init__.py tasks/checkout-recovery/environment/hooks.py tasks/checkout-recovery/environment/server.py tasks/checkout-recovery/environment/install.py /tests/payload/environment/
COPY tasks/checkout-recovery/provenance/autowfbench-source.json /tests/payload/provenance/
RUN python3 /tests/payload/environment/install.py
ENV PYTHONPATH=/tests/core:/tests:/tests/payload/vendor/autowfbench AUTOWFBENCH_ROOT=/tests/payload/vendor/autowfbench
RUN python3 -c "from payload.environment.hooks import plan, prepare, snapshot; from payload.evaluation.evaluator import evaluate; from sapi_config_lab.execute.agency import make_handler"
