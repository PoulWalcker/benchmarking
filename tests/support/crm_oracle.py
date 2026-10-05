"""Trusted native CRM control, not given to model authoring or candidate image."""

from sapi_config_lab.paths import workspace_root
from sapi_config_lab.workflow.profile import read

ROOT = workspace_root()


def oracle_config(contract):
    cfg = read(ROOT / "configs/01-invoice-total.yaml")

    def ref(path):
        return {"ref": "steps." + path}

    changes = {
        name: ref("facts.value.value." + name)
        for name in ["budget_aed", "timeline_weeks", "volume", "languages", "crm", "channel", "human_handoff"]
    }
    changes.update(status="Qualified", next_action="discovery_call", owner="sales_coordinator")
    steps = [
        ("inquiry", "inquiry.read", {}),
        ("identity", "json.parse", {"text": ref("inquiry.result_json")}),
        ("documents", "documents.read", {}),
        ("research", "research.read", {}),
        (
            "clarify",
            "customer.ask",
            {
                "questions": [
                    "What are your budget, timeline, volume, languages, CRM, channel and human handoff requirements?"
                ]
            },
        ),
        ("facts", "json.parse", {"text": ref("clarify.result_json")}),
        ("before", "crm.read", {}),
        ("update", "crm.update", {"changes": changes}),
        (
            "followup",
            "followup.create",
            {"lead_id": ref("identity.value.value.lead_id"), "type": "discovery_call", "status": "pending_scheduling"},
        ),
        (
            "respond",
            "customer.send",
            {
                "recipient": ref("identity.value.value.contact"),
                "body": "Your stated requirements appear suitable for discovery. Salesforce integration is subject to assessment; pricing, dates, accuracy and compliance are not guaranteed. Please share availability for a discovery call; scheduling is pending.",
            },
        ),
        ("readback", "crm.read", {}),
        (
            "report",
            "report.evidence",
            {
                "narrative": "The requested actions qualify the existing lead, preserve its identity, create a discovery follow-up pending scheduling and send the customer a bounded response. Consult the actual receipts below for success or failure. Next: sales_coordinator should agree availability and assess integration, scope, pricing, delivery dates and compliance before any commitment.",
                "evidence": [
                    ref(s + ".result_json")
                    for s in [
                        "inquiry",
                        "documents",
                        "research",
                        "clarify",
                        "update",
                        "followup",
                        "respond",
                        "readback",
                    ]
                ],
            },
        ),
    ]
    w = cfg["workflow"]
    w.update(
        id="crm-reference",
        inputs={},
        acceptance="Original CRM state and evidence verification",
        steps=[{"id": sid, "kind": "Script", "uses": op, "with": args} for sid, op, args in steps],
        dependencies=[[a[0], b[0]] for a, b in zip(steps, steps[1:])],
        output={"final_answer": ref("report.final_answer")},
    )
    cfg["activation"]["workflow_ref"] = {"id": w["id"], "revision": 1}
    cfg["execution"]["deadline_seconds"] = 120
    return cfg
