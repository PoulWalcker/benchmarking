#!/bin/sh
# Build explicit images only; Harbor owns all trial lifecycle and task directories.
set -eu
cd "$(dirname "$0")/../.."
task_core="$PWD/src/sapi_config_lab"
task_verification="$PWD/verification"
task_identity=$(mktemp -d)
trap 'rm -rf "$task_identity"' EXIT HUP INT TERM
python -c 'import json,sys; from sapi_config_lab.coordinate.provenance import source_manifest; json.dump(source_manifest(), sys.stdout, sort_keys=True)' > "$task_identity/source-manifest.json"
docker build -f "$task_core/harbor_integration/runtime/Dockerfile" -t sapi-native-runtime:phase1 .
for target in invoice-total-public checkout-recovery-public invoice-total-verifier checkout-recovery-verifier; do
  docker build -f infra/native/Dockerfile --build-context "sapi-identity=$task_identity" --build-context "sapi-core=$task_core" --build-context "sapi-verification=$task_verification" --target "$target" -t "sapi-native-$target:phase1" .
done

# Native research-report images.
for target in research-report-public research-report-verifier; do
  docker build -f infra/native/Dockerfile --build-context "sapi-identity=$task_identity" --build-context "sapi-core=$task_core" --build-context "sapi-verification=$task_verification" --target "$target" -t "sapi-native-$target:phase1" .
done
