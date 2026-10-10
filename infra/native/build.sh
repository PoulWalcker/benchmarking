#!/bin/sh
# Build explicit images only; Harbor owns all trial lifecycle and task directories.
set -eu
export DOCKER_BUILDKIT=1
cd "$(dirname "$0")/../.."
task_names=$(python -c 'import sys; from pathlib import Path; from sapi_config_lab.coordinate.native_tasks import select_tasks, public_sources
try:
    root = Path("tasks")
    tasks = select_tasks(root, sys.argv[1:] or sorted(p.parent.name for p in root.glob("*/task.toml")))
    errors = []
    for task in tasks:
        try:
            public_sources(task)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
except (ValueError, OSError) as error:
    print(error, file=sys.stderr)
    sys.exit(2)
print("\n".join(task.name for task in tasks))' "$@")
task_core="$PWD/src/sapi_config_lab"
task_verification="$PWD/verification"
task_identity=$(mktemp -d)
trap 'rm -rf "$task_identity"' EXIT HUP INT TERM
python -c 'import json,sys; from sapi_config_lab.coordinate.provenance import source_manifest; json.dump(source_manifest(), sys.stdout, sort_keys=True)' > "$task_identity/source-manifest.json"
docker build -f "$task_core/harbor_integration/runtime/Dockerfile" -t sapi-native-runtime:phase1 .
docker build -f infra/native/Dockerfile --build-context "sapi-identity=$task_identity" --build-context "sapi-core=$task_core" --build-context "sapi-verification=$task_verification" --target public -t sapi-native-public-base:phase1 .
docker build -f infra/native/Dockerfile --build-context "sapi-identity=$task_identity" --build-context "sapi-core=$task_core" --build-context "sapi-verification=$task_verification" --target core -t sapi-native-core:phase1 .
for name in $task_names; do
  for target in public verifier; do
    docker build -f "tasks/$name/images.Dockerfile" --target "$target" -t "sapi-native-$name-$target:phase1" "tasks/$name"
  done
done
