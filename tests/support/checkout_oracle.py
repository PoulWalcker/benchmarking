"""Trusted scripted calibration; excluded from candidate Docker image."""

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.profile import read

ROOT = workspace_root()


def oracle_config(contract):
    """Explicit scripted calibration, never supplied in the authoring prompt."""
    cfg = read(ROOT / "configs/01-invoice-total.yaml")
    w = cfg["workflow"]
    w.update(id="checkout-reference", inputs={}, acceptance="Scripted calibration of original simulator")
    steps = [
        ("incident", "incident.read", {}),
        ("source", "source.read", {}),
        ("before", "tests.run", {}),
        ("patch", "checkout.patch", {"old": "charge_card(currency, amount)", "new": "charge_card(amount, currency)"}),
        ("after", "tests.run", {}),
    ]
    w["steps"] = [{"id": sid, "kind": "Script", "uses": op, "with": args} for sid, op, args in steps]
    w["dependencies"] = [[a[0], b[0]] for a, b in zip(steps, steps[1:])]
    text = (
        "Checkout passed the amount and currency arguments to charge_card in reverse order on the EUR branch. "
        "EUR transactions failed; previously working USD transactions must continue working. "
        "The patch swaps the arguments in that call and preserves validation. "
        "The attached before and after tool receipts record verification; no production system was modified."
    )
    w["output"] = {"final_answer": text, "incident_summary": text}
    cfg["activation"]["workflow_ref"] = {"id": w["id"], "revision": 1}
    cfg["execution"]["deadline_seconds"] = 120
    return cfg
