"""Metamorphism-preserving fresh inputs for the four composed fixture benchmarks."""

import copy
import secrets


def composed_fixtures(original_cases: dict) -> dict:
    """Fresh values for the evaluator-only fixtures, staged after the prompt is frozen.

    Replacements are stable, so duplicate-ID controls and sibling metamorphisms survive.
    """
    cases = copy.deepcopy(original_cases)
    token = secrets.token_hex(6).upper()
    increment = secrets.randbelow(400) + 31

    def transform(value, *, field="", positive=False):
        if isinstance(value, list):
            return [transform(item, field=field, positive=positive) for item in value]
        if isinstance(value, dict):
            return {key: transform(item, field=key, positive=positive) for key, item in value.items()}
        if isinstance(value, str):
            if field in {"id", "required_order_id"}:
                return token + "-" + value
            if field in {"text", "product_material", "marketing_material"} and value:
                return "Fixture " + token + ": " + value
        if field == "amount_minor" and positive and isinstance(value, int) and 0 < value < 1_000_000:
            return value + increment
        return value

    for kind in ("positive", "negative"):
        for case in cases[kind]:
            original = case["inputs"]
            case["inputs"] = transform(original, positive=kind == "positive")
            if "required_order_id" in original:
                # Keep the order named in the ticket aligned with the projected request.
                case["inputs"]["ticket"]["text"] = case["inputs"]["ticket"]["text"].replace(
                    original["required_order_id"], case["inputs"]["required_order_id"]
                )
    return cases
