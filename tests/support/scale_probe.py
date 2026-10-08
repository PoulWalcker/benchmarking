"""Synthetic static graph probe, not an n8n throughput benchmark."""

import copy
import json
import subprocess
import time

from sapi_config_lab import profile
from sapi_config_lab.compile import n8n as compiler
from sapi_config_lab.paths import workspace_root
from tests.support.invoice import CATALOG, OPERATION_SOURCE

ROOT = workspace_root()


def make_config(leaves=64):
    if leaves < 2 or leaves & (leaves - 1):
        raise ValueError("Use a power of two >= 2")
    cfg = copy.deepcopy(profile.read(ROOT / "tests/support/graphs/branch.yaml"))
    cfg.pop("actors")
    w = cfg["workflow"]
    w["id"] = "static-scale-probe"
    w["inputs"] = {}
    w["steps"], w["dependencies"] = [], []
    current = []
    for i in range(leaves):
        sid = f"leaf-{i}"
        current.append(sid)
        w["steps"].append(
            {"id": sid, "kind": "LLM", "uses": "research.product", "with": {"material": f"SOURCE_{i:03}"}}
        )
    level = 0
    while len(current) > 1:
        following = []
        for i in range(0, len(current), 2):
            sid = f"join-{level}-{i // 2}"
            left, right = current[i : i + 2]
            w["steps"].append(
                {
                    "id": sid,
                    "kind": "Script",
                    "uses": "research.combine",
                    "join": "all_terminal",
                    "with": {"product": {"ref": f"steps.{left}"}, "marketing": {"ref": f"steps.{right}"}},
                }
            )
            w["dependencies"].extend([[left, sid], [right, sid]])
            following.append(sid)
        current, level = following, level + 1
    w["output"] = {"ref": f"steps.{current[0]}"}
    w["acceptance"] = "All source sentinels occur exactly once in the output tree."
    cfg["activation"]["workflow_ref"]["id"] = w["id"]
    return cfg


def evidence_leaves(value):
    if "evidence" in value:
        return [value["evidence"]]
    return evidence_leaves(value["product"]) + evidence_leaves(value["marketing"])


def main():
    cfg = make_config()
    bindings = profile.read(CATALOG)["operations"]
    start = time.perf_counter()
    artifact, _ = compiler.compile_n8n(cfg, bindings, operation_source=OPERATION_SOURCE)
    elapsed = time.perf_counter() - start
    path = ROOT / "validation/static-scale-probe.n8n.json"
    raw = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    path.write_text(raw)
    run = subprocess.run(
        ["node", str(ROOT / "tests/support/run-export.mjs"), str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    result = json.loads(run.stdout)
    evidence = evidence_leaves(result["output"])
    expected = [f"SOURCE_{i:03}" for i in range(64)]
    assert evidence == expected
    assert len(result["trace"]) == len(cfg["workflow"]["steps"])
    summary = {
        "logical_steps": len(cfg["workflow"]["steps"]),
        "n8n_nodes": len(artifact["nodes"]),
        "n8n_export_bytes": len(raw.encode()),
        "compile_seconds_observed": round(elapsed, 4),
        "source_leaves_expected": 64,
        "source_leaves_observed": len(evidence),
        "trace_events": len(result["trace"]),
        "all_sources_exactly_once": True,
        "execution_engine": "run-export.mjs (minimal local JS driver, NOT n8n)",
        "n8n_runtime_tested": False,
        "production_scalability_proven": False,
    }
    (ROOT / "validation/scale-results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
