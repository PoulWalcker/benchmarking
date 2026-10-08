#!/bin/sh
set -eu
exec python3 -m sapi_config_lab.coordinate.benchmark_worker
