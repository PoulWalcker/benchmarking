FROM sapi-native-public-base:phase1 AS public
COPY instruction.md public/authoring-notes.md bindings.yaml public/authoring-prompt.txt /app/public/

FROM sapi-native-core:phase1 AS verifier
COPY experiment.py /tests/payload/experiment.py
COPY tests/calibration/judge-reply.json /tests/calibration/judge-reply.json
COPY bindings.yaml operations.js /tests/payload/
COPY evaluation /tests/payload/evaluation
COPY environment/__init__.py environment/hooks.py environment/server.py environment/install.py /tests/payload/environment/
COPY provenance/autowfbench-source.json /tests/payload/provenance/
RUN python3 /tests/payload/environment/install.py
ENV PYTHONPATH=/tests/core:/tests:/tests/payload/vendor/autowfbench AUTOWFBENCH_ROOT=/tests/payload/vendor/autowfbench
RUN python3 -c "from payload.environment.hooks import plan, prepare, snapshot; from payload.evaluation.evaluator import evaluate; from sapi_config_lab.execute.agency import make_handler"
