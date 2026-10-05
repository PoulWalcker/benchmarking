"""Evaluator-only oracle controls, never model solutions or candidate prompt data."""

CHECKOUT_ORACLE_ACTIONS = (
    ("incident.read", {}),
    ("source.read", {}),
    ("tests.run", {}),
    ("checkout.patch", {"old": "charge_card(currency, amount)", "new": "charge_card(amount, currency)"}),
    ("tests.run", {}),
)

CHECKOUT_INCOMPLETE_ACTIONS = (
    ("incident.read", {}),
    ("source.read", {}),
    ("tests.run", {}),
)
