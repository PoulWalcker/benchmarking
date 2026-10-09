#!/bin/sh
set -eu
sha256sum -c /opt/native-sources.sha256 > /dev/null
cp /opt/native-sources.sha256 /logs/verifier/native-sources.sha256
python3 /opt/sapi-submission.py verify
exec python3 /tests/main.py
