# Clean runtime: no checkout or benchmark files enter any ancestor layer.
FROM n8nio/n8n:2.41.5@sha256:6f532d3b819cfe66c6a13c5375228f312c53df1abaa11a377eb8c3d3f105c363 AS n8n
FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN apk add --no-cache python3=3.14.8-r0 py3-yaml=6.0.3-r2 bash=5.3.9-r1 libstdc++=15.2.0-r5 libgcc=15.2.0-r5 ca-certificates=20260909-r0 tini=0.19.0-r3 gcompat=1.1.0-r4 tzdata=2026d-r0
COPY --from=n8n /usr/local /usr/local
COPY --from=n8n /usr/bin/node /usr/bin/node
ENV N8N_DIAGNOSTICS_ENABLED=false N8N_VERSION_NOTIFICATIONS_ENABLED=false N8N_TEMPLATES_ENABLED=false N8N_RUNNERS_ENABLED=false N8N_LOG_LEVEL=info N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true EXECUTIONS_DATA_SAVE_ON_SUCCESS=all EXECUTIONS_DATA_SAVE_ON_ERROR=all EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS=true
WORKDIR /app
ENTRYPOINT []
CMD ["sleep", "infinity"]
