#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
# The verifier states what must run, the lab runtime runs it and records evidence,
# and the verifier judges only that record. Bytecode goes to a fresh prefix so no
# precompiled file left in the image is imported in place of the packaged source.
export PYTHONPYCACHEPREFIX="$(mktemp -d)"
if python3 /tests/verify.py plan --scenario @SCENARIO@ --config /app/submission/config.yaml --output /logs/verifier/plan.json; then
  python3 -m sapi_config_lab.coordinate.observe --plan /logs/verifier/plan.json \
    --submission /app/submission/config.yaml --evidence /logs/verifier
fi
if python3 /tests/verify.py evaluate --scenario @SCENARIO@ --config /app/submission/config.yaml \
  --evidence /logs/verifier --runtime-src /app/lab/src; then
  printf '1\n' > /logs/verifier/reward.txt
fi
