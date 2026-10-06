#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --locked --extra harbor --extra benchmark sapi-lab generate "$@"
