"""Prompt-only YAML experiment helpers; no expected-answer computation."""

from __future__ import annotations

import json
from pathlib import Path

from sapi_config_lab.coordinate.provenance import source_manifest
from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()


def fingerprints() -> dict[str, str]:
    """Freeze the same complete source inventory used by the control suite."""
    return source_manifest()


def prepare_tasks(destination: Path, image: str, *, scenarios: tuple[str, ...] | None = None) -> dict:
    """Generation entry into the shared packager; no reference YAML or solutions."""
    from sapi_config_lab.coordinate.packages import stage_tasks

    return stage_tasks(destination, mode="generation", image=image, scenarios=scenarios)


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
