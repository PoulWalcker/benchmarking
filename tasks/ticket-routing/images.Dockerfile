FROM sapi-native-public-base:phase1 AS public
COPY instruction.md \
     task.md \
     bindings.yaml \
     /app/public/

FROM sapi-native-core:phase1 AS verifier
COPY experiment.py /tests/payload/experiment.py
COPY bindings.yaml \
     operations.js \
     cases.json \
     /tests/payload/
COPY evaluation /tests/payload/evaluation
RUN python3 -c "from payload.evaluation.evaluator import plan, evaluate; from sapi_config_lab.coordinate.task_worker import run_task"
