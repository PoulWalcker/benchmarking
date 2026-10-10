# Only these positive public inputs enter candidate image ancestry.
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS public
RUN apk add --no-cache python3=3.14.8-r0 py3-yaml=6.0.3-r2 bash=5.3.9-r1 && adduser -D -u 1000 author && mkdir -p /app/submission /submission && chown author /app/submission && chmod 700 /submission
COPY --from=sapi-core harbor_integration/submission.py /opt/sapi-submission.py
COPY generation/PROFILE.md generation/FORMAT.md /app/public/
WORKDIR /app
USER 1000
CMD ["sleep", "infinity"]

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
