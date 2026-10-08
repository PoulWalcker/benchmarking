"""Explicit retained benchmark inputs for tests of generic mechanisms."""

import json

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.paths import workspace_root

DIRECTORY = workspace_root() / "benchmarks/01-invoice-total"
CATALOG = DIRECTORY / "bindings.yaml"
OPERATION_SOURCE = (DIRECTORY / "operations.js").read_text()


def fixture():
    benchmark = load_benchmark(DIRECTORY.parent, DIRECTORY)
    hooks = load_entrypoints(benchmark, freeze_identity(benchmark, {}))
    return hooks.plan.__globals__["_fixture"]({})


def cases():
    return json.loads((DIRECTORY / "cases.json").read_text())


def card():
    return fixture().rubric
