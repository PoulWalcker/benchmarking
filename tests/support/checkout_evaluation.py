"""Trusted checkout modules selected through their declared manifest closure."""

from importlib import import_module

from sapi_config_lab.benchmark import load_benchmark
from sapi_config_lab.benchmark_loading import freeze_identity, load_entrypoints
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
BENCHMARK = load_benchmark(ROOT / "benchmarks", ROOT / "benchmarks/10-checkout-recovery")
ENTRYPOINTS = load_entrypoints(BENCHMARK, freeze_identity(BENCHMARK, {}))
SCORING = import_module(ENTRYPOINTS.package + ".evaluation.scoring")
CALIBRATION = import_module(ENTRYPOINTS.package + ".evaluation.calibration")
SERVER = import_module(ENTRYPOINTS.package + ".environment.server")
