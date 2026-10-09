"""Unpaid Codex-wrapper response contract; never substitutes for measured model quality."""

import json
from pathlib import Path


def write_once(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write("\n")


def transport_for(directory: Path, mode="success"):
    calls = 0

    def transport(upstream, prompt, timeout, maximum):
        nonlocal calls
        calls += 1
        write_once(
            directory / f"call-{calls}.json",
            {
                "prompt": prompt,
                "timeout": timeout,
                "transport": "deterministic-fake",
                "paid_dispatch": False,
            },
        )
        if mode == "timeout":
            raise TimeoutError("Deterministic unknown-outcome transport fault")
        if "OPERATION: incident.plan\n" in prompt:
            output = {
                "old": "charge_card(currency, amount)",
                "new": "charge_card(amount, currency)",
            }
        elif "OPERATION: incident.summarize\n" in prompt:
            text = "EUR checkout reversed amount and currency in charge_card. Swapped the arguments; before/after test receipts verify EUR repair and USD preservation. No production deployment was performed."
            output = {"final_answer": text, "incident_summary": text}
        else:
            raise ValueError("No fake response for this operation")
        reply = {
            "ok": mode != "failure",
            "exit_code": 1 if mode == "failure" else 0,
            "output": json.dumps(output) if mode != "malformed" else "{}",
            "stderr": "model: " + ("wrong-model" if mode == "wrong-model" else "native-fake-codex") + "\n",
        }
        write_once(directory / f"reply-{calls}.json", reply)
        return json.dumps(reply).encode()

    return transport
