"""Preserve declared public authoring material for selected versioned benchmarks."""

from pathlib import Path

import yaml

from sapi_config_lab.benchmark import Benchmark
from sapi_config_lab.profile import read


def generation_prompt(root: Path, benchmark: Benchmark, catalog: str) -> str:
    authoring = benchmark.config.get("authoring", {})
    if catalog not in authoring.get("catalogs", ("full", "scenario")):
        raise ValueError("A reduced catalog applies to fixture scenarios only: " + benchmark.name)
    files = {item.destination: item.source for item in benchmark.public}
    if "prompt" in authoring:
        return files[authoring["prompt"]].read_text()
    text = files[benchmark.bindings].read_text()
    if catalog == "scenario":
        used = {step["uses"] for step in read(benchmark.reference.source)["workflow"]["steps"]}
        kept, section, keep = [], "", True
        for line in text.splitlines(keepends=True):
            if line[:1].isalpha():
                section, keep = line.split(":", 1)[0], True
            elif section == "operations" and line.startswith("  ") and line[2:3].isalpha():
                keep = line.strip().removesuffix(":") in used
            if keep:
                kept.append(line)
        reduced = "".join(kept)
        full = yaml.safe_load(text)
        if yaml.safe_load(reduced) != {
            **full,
            "operations": {k: v for k, v in full["operations"].items() if k in used},
        }:
            raise ValueError("Catalog reduction changed operation declarations")
        text = reduced
    return (
        "TASK\n"
        + files["task.md"].read_text().removesuffix("\n")
        + "\n\nFORMAT\n"
        + (root / "generation/FORMAT.md").read_text()
        + "\n\nPROFILE\n"
        + (root / "generation/PROFILE.md").read_text()
        + "\n\nOPERATION CATALOG\n"
        + text
    )
