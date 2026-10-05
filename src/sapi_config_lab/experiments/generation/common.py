"""Prompt-only YAML experiment helpers; no expected-answer computation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from sapi_config_lab.experiments.provenance import source_manifest
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def fingerprints() -> dict[str, str]:
    """Freeze the same complete source inventory used by the control suite."""
    return source_manifest()


def prepare_tasks(destination: Path, image: str, *, scenarios: tuple[str, ...] | None = None) -> dict:
    """Generation entry into the shared packager; no reference YAML or solutions."""
    from sapi_config_lab.experiments.tasks import stage_tasks

    return stage_tasks(destination, mode="generation", image=image, scenarios=scenarios)


def audit_stderr(stderr: str) -> dict:
    """Keep limited CLI metadata, never persist wrapper stderr or credentials.

    This is observational, not a security boundary. The existing wrapper does
    not disable tools. A prompt asks for no tool use; recognized calls reject
    the attempt. Unknown CLI formats remain a documented isolation limitation.
    """
    model = re.search(r"(?m)^model:\s*([A-Za-z0-9_.-]+)\s*$", stderr)
    tokens = re.search(r"(?m)^tokens used\s*\n([\d,]+)\s*$", stderr)
    markers = []
    for name, pattern in {
        "shell": r"(?m)^exec(?:\s|$)",
        "tool": r"(?m)^tool\s+",
        "file_edit": r"(?m)^(?:file update|apply_patch)(?:\s|$)",
        "web": r"(?mi)^(?:web search|searching the web|searched the web)(?:\s|$)",
    }.items():
        if re.search(pattern, stderr):
            markers.append(name)
    return {
        "model": model.group(1) if model else None,
        "cli_reported_tokens": int(tokens.group(1).replace(",", "")) if tokens else None,
        "observed_tool_markers": markers,
        "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
        "tool_isolation": "prompt restriction plus stderr audit; not enforced by wrapper",
    }


def summarize_trials(job: Path) -> list[dict]:
    trials = []
    for path in sorted(job.glob("*/result.json")):
        native = json.loads(path.read_text())
        agent_path = path.parent / "agent/generation.json"
        acceptance_path = path.parent / "verifier/report.json"
        agent = json.loads(agent_path.read_text()) if agent_path.exists() else {}
        acceptance = json.loads(acceptance_path.read_text()) if acceptance_path.exists() else None
        passed = (
            agent.get("status") == "submitted"
            and not native.get("exception_info")
            and (native.get("verifier_result") or {}).get("rewards") == {"reward": 1.0}
            and acceptance
            and acceptance.get("passed") is True
        )
        stage = None
        if not passed:
            if agent.get("status") != "submitted":
                stage = "generation_or_transport"
            elif not acceptance:
                stage = "test_infrastructure"
            elif not acceptance.get("cases"):
                stage = "yaml_parsing_or_definition"
            else:
                stage = "acceptance"
                for case in acceptance["cases"]:
                    if case.get("passed"):
                        continue
                    case_dir = Path(case["artifacts"]).name
                    case_path = path.parent / "verifier/cases" / case_dir / "case.json"
                    if case_path.exists():
                        status = json.loads(case_path.read_text()).get("status")
                        if status == "compile_error":
                            stage = "compilation"
                        elif status in {"error", "import_error"} and case.get("kind") == "positive":
                            stage = "import_or_execution"
                    break
        trials.append(
            {
                "scenario": native.get("task_name"),
                "passed": bool(passed),
                "failure_stage": stage,
                "generation": agent,
                "rewards": (native.get("verifier_result") or {}).get("rewards"),
                "exception": native.get("exception_info"),
                "acceptance": acceptance,
                "result_path": str(path),
            }
        )
    return trials
