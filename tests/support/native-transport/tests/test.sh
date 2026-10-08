#!/bin/sh
set -eu
n8n --version > /logs/verifier/runtime-versions.txt
node --version >> /logs/verifier/runtime-versions.txt
python3 --version >> /logs/verifier/runtime-versions.txt
apk info -v >> /logs/verifier/runtime-versions.txt
python3 -m sapi_config_lab.coordinate.transport --fixtures /tests --artifacts /logs/verifier/transport
printf '1\n' > /logs/verifier/reward.txt
