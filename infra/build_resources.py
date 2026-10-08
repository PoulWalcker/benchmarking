"""Include only declared benchmark closures and unchanged generation prompts in wheels."""

import importlib.util
from pathlib import Path
import sys

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BenchmarkResources(BuildHookInterface):
    """Manifest ownership supplies resources; installation never registers business code."""

    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        root = Path(self.root)
        spec = importlib.util.spec_from_file_location("_sapi_build_manifest", root / "src/sapi_config_lab/benchmark.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        files = {root / "generation" / name for name in ("FORMAT.md", "PROFILE.md")}
        for benchmark in module.discover_benchmarks(root / "benchmarks"):
            files.add(benchmark.directory / "scenario.json")
            files.update(item.source for item in benchmark.files)
        for source in sorted(files):
            build_data["force_include"][str(source)] = (
                "sapi_config_lab/resources/" + source.relative_to(root).as_posix()
            )
