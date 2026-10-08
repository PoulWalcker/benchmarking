"""Legacy digest decision for explicit compatibility callers; lifecycle has no default decision."""

from __future__ import annotations

from sapi_config_lab.contracts import Document, ExecutionRecord


def digest_acceptance(config: Document, record: ExecutionRecord) -> Document:
    """The `digest.acceptance_v1` test decision for a daily-digest candidate."""
    findings = []
    steps = config["workflow"]["steps"]
    roles = {step["uses"]: step for step in steps}
    if len(steps) != 3 or set(roles) != {"digest.prepare", "digest.summarize", "digest.preview"}:
        findings.append("Digest candidate must contain prepare, summarize and preview once each")
    else:
        prepare, summary, preview = (roles[name] for name in ("digest.prepare", "digest.summarize", "digest.preview"))
        expected = [
            (prepare, {"articles": {"ref": "inputs.articles"}}),
            (summary, {"articles": {"ref": f"steps.{prepare['id']}.articles"}}),
            (preview, {"summary": {"ref": "steps." + summary["id"]}}),
        ]
        if any(step["with"] != arguments or "when" in step for step, arguments in expected):
            findings.append("Digest must preserve the supplied article source through prepare, summarize and preview")
        if config["workflow"]["output"] != {"ref": "steps." + preview["id"]}:
            findings.append("Digest output must reference the actual preview result")
    output = record.get("output")
    if record.get("status") != "success":
        findings.append("Native execution did not succeed")
    if not isinstance(output, dict):
        findings.append("Digest output is not an object")
    else:
        if output.get("mode") != "preview":
            findings.append("Digest must be a preview")
        if not isinstance(output.get("text"), str) or not output["text"].strip():
            findings.append("Digest summary must be nonempty")
        articles = config["workflow"]["inputs"].get("articles", [])
        expected_ids = [a.get("id") for a in articles if isinstance(a, dict)]
        if not expected_ids or output.get("article_ids") != expected_ids:
            findings.append("Digest article IDs must match the supplied articles in order")
    return {"verifier": "digest.acceptance_v1", "passed": not findings, "findings": findings}
