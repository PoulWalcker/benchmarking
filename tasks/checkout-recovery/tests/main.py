"""Native checkout composition with deterministic unpaid runtime transport."""

from http.server import HTTPServer
import os
from pathlib import Path
import threading
import time

from calibration_transport import adapt
from fake_bridge import transport_for
from payload.environment.hooks import plan, prepare, snapshot
from payload.evaluation.evaluator import evaluate
from payload.evaluation.scoring import freeze_contract, recorded_run_log
from payload.experiment import runtime_options
import yaml

from sapi_config_lab.contracts import CompileOptions, OutputArtifact
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.coordinate.benchmark_worker import admit, run_task
from sapi_config_lab.coordinate.native_record import record
from sapi_config_lab.evaluate.records import NOT_EVALUATED, validate_result
from sapi_config_lab.evidence import write_json
from sapi_config_lab.execute.agency import make_handler
from sapi_config_lab.pinned_source import PinnedSource
from sapi_config_lab.profile import check, read, read_bindings

ROOT = Path("/tests/payload")
OUT = Path("/logs/verifier")
SUBMISSION = Path("/submission/config.yaml")


def evaluate_calibrated(evidence: Path, options: dict) -> dict:
    if options["native_mode"] == "live":
        return dict(evaluate(evidence, {**options, "dispatch": False}))
    contract = freeze_contract(
        PinnedSource(ROOT / "provenance/autowfbench-source.json", ROOT / "vendor/autowfbench"),
        "production-checkout-recovery",
        judge_mode="demo",
        artifact=OutputArtifact("incident_summary", "incident-summary.md"),
    )
    run_log = recorded_run_log(contract, evidence)
    reply, audit = adapt(Path("/tests/calibration/judge-reply.json"), contract, run_log)
    if os.environ.get("SAPI_NATIVE_JUDGE_MODE", "calibration") == "none":
        reply = None
        audit["status"] = "disabled"
    write_json(OUT / "calibration-transport.json", audit)
    result = evaluate(evidence, {**options, "judgement": reply, "dispatch": False})
    if reply is not None:
        write_json(OUT / "evaluation/judge-reply.json", reply)
    return dict(result)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    native_mode = os.environ.get("SAPI_NATIVE_MODE", "control")
    if native_mode not in {"control", "admission", "live"}:
        raise ValueError("Unknown checkout native mode")
    admission = native_mode == "admission" or os.environ.get("SAPI_HOSTED_ADMISSION") == "1"
    native_mode = "admission" if admission else native_mode
    try:
        config = read(SUBMISSION)
        llms = [step for step in config["workflow"]["steps"] if step["kind"] == "LLM"]
    except OSError, ValueError, KeyError, TypeError, yaml.YAMLError:
        config, llms = None, []
    options = record(
        "checkout-recovery",
        OUT,
        SUBMISSION,
        runtime_options(
            {
                "native_mode": native_mode,
                "mode": "live" if llms else "stub",
                "selected_case": os.environ.get("SAPI_CASE_NAME"),
                "judge_model": os.environ.get("SAPI_NATIVE_JUDGE_MODEL"),
            }
        ),
    )
    if native_mode == "live" and (not options["judge_model"] or not os.environ.get("SAPI_BRIDGE_URL")):
        raise ValueError("Live checkout requires the protected runtime bridge and explicit judge model")
    if admission:
        report = admit(
            {
                "name": "checkout-recovery",
                "operations": "operations.js",
                "bindings": "bindings.yaml",
                "budgets": {"runtime_model_calls": 4},
            },
            ROOT,
            SUBMISSION,
            {"deadline_seconds": 120},
        )
        (OUT / "evaluation").mkdir(exist_ok=True)
        write_json(OUT / "evaluation/report.json", report)
        write_json(OUT / "result.json", NOT_EVALUATED)
        (OUT / "reward.txt").write_text("1\n" if report["passed"] else "0\n")
        return 0
    try:
        bindings = read_bindings(ROOT / "bindings.yaml")
        N8nBackend(operation_source=(ROOT / "operations.js").read_text()).compile(
            config,
            bindings,
            CompileOptions(
                llm_mode="live", bridge_url="http://127.0.0.1:8765", operation_url="http://simulator:8000/tools"
            ),
        )
        check(len(llms) <= 4, "Runtime cap exceeded")
        check(config["execution"]["deadline_seconds"] == 120, "Original deadline required")
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        write_json(OUT / "admission.json", {"passed": False, "error": type(error).__name__})
        write_json(
            OUT / "result.json",
            validate_result({"execution": None, "acceptance": False, "quality": None, "harbor_reward": None}),
        )
        return 0
    bridge = None
    if llms and native_mode == "control":
        budget = {
            "max_attempts": len(llms),
            "operations": {op: sum(step["uses"] == op for step in llms) for op in {step["uses"] for step in llms}},
            "model": "native-fake-codex",
            "occurrences": {
                f"{config['workflow']['id']}/r{config['workflow']['revision']}/{step['id']}": step["uses"]
                for step in llms
            },
            "expires_at": time.time() + 120,
        }
        bridge = HTTPServer(
            ("127.0.0.1", 8765),
            make_handler(
                bindings,
                "fake://native-control",
                10,
                OUT / "evidence/runtime-dispatch.jsonl",
                budget,
                True,
                transport=transport_for(
                    OUT / "evidence/runtime-model", os.environ.get("SAPI_NATIVE_FAKE_MODE", "success")
                ),
            ),
        )
        threading.Thread(target=bridge.serve_forever, daemon=True).start()
        os.environ["SAPI_BRIDGE_URL"] = "http://127.0.0.1:8765"
    try:
        return run_task(
            ROOT,
            OUT,
            SUBMISSION,
            options,
            {"plan": plan, "prepare": prepare, "snapshot": snapshot, "evaluate": evaluate_calibrated},
        )
    finally:
        if bridge:
            bridge.shutdown()
            bridge.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
