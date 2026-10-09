# Only these positive public inputs enter candidate image ancestry.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS public
RUN apk add --no-cache python3=3.14.8-r0 py3-yaml=6.0.3-r2 bash=5.3.9-r1 && adduser -D -u 1000 author && mkdir -p /app/submission /submission && chown author /app/submission && chmod 700 /submission
COPY src/sapi_config_lab/harbor_integration/submission.py /opt/sapi-submission.py
COPY generation/PROFILE.md generation/FORMAT.md /app/public/
WORKDIR /app
USER 1000
CMD ["sleep", "infinity"]

FROM public AS invoice-total-public
COPY benchmarks/01-invoice-total/instruction.md benchmarks/01-invoice-total/task.md benchmarks/01-invoice-total/bindings.yaml /app/public/

FROM public AS checkout-recovery-public
COPY benchmarks/10-checkout-recovery/instruction.md benchmarks/10-checkout-recovery/authoring-notes.md benchmarks/10-checkout-recovery/bindings.yaml benchmarks/10-checkout-recovery/authoring-prompt.txt /app/public/

# Trusted stages use the existing pinned n8n runtime, never a public descendant.
FROM sapi-native-runtime:phase1 AS core
COPY src/sapi_config_lab/__init__.py src/sapi_config_lab/contracts.py src/sapi_config_lab/evidence.py src/sapi_config_lab/paths.py src/sapi_config_lab/profile.py src/sapi_config_lab/pinned_source.py src/sapi_config_lab/net.py src/sapi_config_lab/wrapper_audit.py /tests/core/sapi_config_lab/
COPY src/sapi_config_lab/compile /tests/core/sapi_config_lab/compile
COPY src/sapi_config_lab/execute/__init__.py src/sapi_config_lab/execute/n8n.py src/sapi_config_lab/execute/agency.py src/sapi_config_lab/execute/host.py src/sapi_config_lab/execute/agency-prompt.md /tests/core/sapi_config_lab/execute/
COPY src/sapi_config_lab/evaluate/__init__.py src/sapi_config_lab/evaluate/records.py /tests/core/sapi_config_lab/evaluate/
COPY src/sapi_config_lab/coordinate/__init__.py src/sapi_config_lab/coordinate/backend.py src/sapi_config_lab/coordinate/cases.py src/sapi_config_lab/coordinate/observe.py src/sapi_config_lab/coordinate/benchmark_worker.py /tests/core/sapi_config_lab/coordinate/
COPY verification /tests/core/verification
COPY src/sapi_config_lab/harbor_integration/submission.py /opt/sapi-submission.py
ENV PYTHONPATH=/tests/core:/tests PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
RUN mkdir -p /submission /logs/artifacts

FROM core AS invoice-total-verifier
COPY benchmarks/01-invoice-total/bindings.yaml benchmarks/01-invoice-total/operations.js benchmarks/01-invoice-total/cases.json /tests/payload/
COPY benchmarks/01-invoice-total/evaluation /tests/payload/evaluation
RUN python3 -c "from payload.evaluation.evaluator import plan, evaluate; from sapi_config_lab.coordinate.benchmark_worker import run_task"

FROM core AS checkout-recovery-verifier
COPY benchmarks/10-checkout-recovery/bindings.yaml benchmarks/10-checkout-recovery/operations.js /tests/payload/
COPY benchmarks/10-checkout-recovery/evaluation /tests/payload/evaluation
COPY benchmarks/10-checkout-recovery/environment/__init__.py benchmarks/10-checkout-recovery/environment/hooks.py benchmarks/10-checkout-recovery/environment/server.py benchmarks/10-checkout-recovery/environment/install.py /tests/payload/environment/
COPY benchmarks/10-checkout-recovery/provenance/autowfbench-source.json /tests/payload/provenance/
RUN python3 /tests/payload/environment/install.py
ENV PYTHONPATH=/tests/core:/tests:/tests/payload/vendor/autowfbench AUTOWFBENCH_ROOT=/tests/payload/vendor/autowfbench
RUN python3 -c "from payload.environment.hooks import plan, prepare, snapshot; from payload.evaluation.evaluator import evaluate; from sapi_config_lab.execute.agency import make_handler"
