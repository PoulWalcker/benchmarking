"""Explicit independent fixture behavior supplied by a benchmark or legacy adapter."""

from collections.abc import Callable
import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING or __package__:
    from .n8n_provenance import rows
else:
    from n8n_provenance import rows


def wrong_output(candidate: dict) -> None:
    final = rows(candidate["run_data"]["Result"][0])[0]
    final["output"] = {"deliberately_wrong": True}
    candidate["output"] = copy.deepcopy(final["output"])
    candidate["result"] = copy.deepcopy(final)


@dataclass(frozen=True)
class FixtureEvaluator:
    """Existing independent business callbacks and the procedure needed to observe them."""

    business: Callable | None = None
    procedure: Literal["case", "refinement", "lifecycle"] = "case"
    guard: Callable[[dict, dict], bool] | None = None
    obligations: Callable | None = None
    prose: Callable | None = None
    fresh: Callable[[dict], dict] | None = None
    corrupt_output: Callable[[dict], None] = wrong_output
    extra_corruptions: tuple[tuple[str, Callable[[dict], None]], ...] = ()

    contract: Any = None
    identity: dict[str, str] | None = None
    rubric: Any = None
