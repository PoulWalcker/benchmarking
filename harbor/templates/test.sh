#!/usr/bin/env bash
set -uo pipefail
mkdir -p /logs/verifier
printf '0\n' > /logs/verifier/reward.txt
if python3 /tests/verify.py --scenario @SCENARIO@ --config /app/submission/config.yaml --report-dir /logs/verifier; then
  printf '1\n' > /logs/verifier/reward.txt
fi
