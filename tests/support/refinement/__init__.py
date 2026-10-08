"""Test-only numeric refinement contracts; no benchmark discovery or runtime imports."""

from pathlib import Path

import yaml

from verification.contracts import equal, require
from verification.refinement import check_refinement_history as generic_history
from verification.refinement import verify_refinement as generic_verify

ROOT = Path(__file__).parent


def definition():
    """Load only this isolated definition."""
    return yaml.safe_load((ROOT / "config.yaml").read_text())


def review(value, target, ceiling):
    """Independently state the numeric acceptance rule."""
    require(type(value) is int, "Missing numeric result")
    errors = []
    if value < target:
        errors.append("Increase value")
    if value > ceiling:
        errors.append("Value exceeds ceiling")
    return {"pass": not errors, "errors": errors}


def check_steps(config, attempt):
    """Reject coherent false reviews without importing the JavaScript bundle."""
    output = attempt["steps"]["draft"]
    require(isinstance(output, dict) and set(output) == {"value"}, "Wrong numeric output")
    inputs = config["workflow"]["inputs"]
    equal(
        attempt["steps"]["check"],
        review(output["value"], inputs["target"], inputs["ceiling"]),
        "Dishonest numeric review",
    )


def check_refinement_history(config, attempts):
    return generic_history(config, attempts, check_steps=check_steps)


def verify_refinement(config, run, *, mode="live"):
    return generic_verify(config, run, check_steps=check_steps, mode=mode)
