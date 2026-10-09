#!/bin/sh
# Build explicit images only; Harbor owns all trial lifecycle and task directories.
set -eu
cd "$(dirname "$0")/../.."
if [ -d src/sapi_config_lab ]; then
  task_core="$PWD/src/sapi_config_lab"
  task_verification="$PWD/verification"
else
  task_core=$(python -c 'import pathlib, sapi_config_lab; print(pathlib.Path(sapi_config_lab.__file__).parent)')
  task_verification=$(python -c 'import pathlib, verification; print(pathlib.Path(verification.__file__).parent)')
fi
docker build -f "$task_core/harbor_integration/runtime/Dockerfile" -t sapi-native-runtime:phase1 .
for target in invoice-total-public checkout-recovery-public invoice-total-verifier checkout-recovery-verifier; do
  docker build -f infra/native/Dockerfile --build-context "sapi-core=$task_core" --build-context "sapi-verification=$task_verification" --target "$target" -t "sapi-native-$target:phase1" .
done
