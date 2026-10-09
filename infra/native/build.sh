#!/bin/sh
# Build explicit images only; Harbor owns all trial lifecycle and task directories.
set -eu
cd "$(dirname "$0")/../.."
docker build -f src/sapi_config_lab/harbor_integration/runtime/Dockerfile -t sapi-native-runtime:phase1 .
for target in invoice-total-public checkout-recovery-public invoice-total-verifier checkout-recovery-verifier; do
  docker build -f infra/native/Dockerfile --target "$target" -t "sapi-native-$target:phase1" .
done
