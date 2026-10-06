"""Rubric cards are scenario data: benchmarks/NN-name/evaluation/rubric.json; cards ask and weigh, never accept."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .contracts import scenario_file
    from .rubric import Criterion, RubricCard, RubricError
else:  # Harbor runs the distributed verifier as a standalone script.
    from contracts import scenario_file
    from rubric import Criterion, RubricCard, RubricError


def card_for(scenario: str) -> RubricCard:
    """Return the card for a scenario; an unknown scenario has no silent default."""
    path = scenario_file(scenario, "rubric.json")
    if path is None:
        raise RubricError("No rubric card for scenario: " + scenario)
    data = json.loads(path.read_text())
    return RubricCard(
        id=data["id"],
        version=data["version"],
        origin=data["origin"],
        criteria=tuple(Criterion(**criterion) for criterion in data["criteria"]),
    )
