"""Trusted container step for hosted scenarios: admit a submission, or run it once against the host's environment."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from sapi_config_lab.contracts import CompileOptions
from sapi_config_lab.coordinate.backend import default_backend
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.evidence import write_json, write_record_json
from sapi_config_lab.profile import Invalid, Unsupported, check, read, read_bindings

SUBMISSION = Path("/app/submission/config.yaml")
TESTS = Path("/tests")
LOGS = Path("/logs/verifier")
# Each of /begin and /finish; /finish includes the host's evaluation.
HTTP_TIMEOUT_SECONDS = 240


def submission_sha256() -> str | None:
    return hashlib.sha256(SUBMISSION.read_bytes()).hexdigest() if SUBMISSION.exists() else None


def admit(limits: dict) -> dict:
    """The unpaid gate before any live run: protocol limits and a compiling definition."""
    report = {
        "schema": "sapi-lab-admission/v1",
        "scenario": limits["scenario"],
        "mode": "stub",
        "passed": False,
        "submission_sha256": submission_sha256(),
    }
    try:
        config = read(SUBMISSION)
        # Compiling validates the definition; placeholder URLs compile, the host supplies real ones at run time.
        default_backend().compile(
            config,
            read_bindings(TESTS / "bindings.yaml"),
            CompileOptions(llm_mode="live", bridge_url="http://localhost:1", operation_url="http://localhost:2/tools"),
        )
        llm_steps = sum(step["kind"] == "LLM" for step in config["workflow"]["steps"])
        check(llm_steps <= limits["runtime_model_calls"], "Runtime cap exceeded")
        check(config["execution"]["deadline_seconds"] == limits["deadline_seconds"], "Original deadline required")
        report["passed"] = True
    except (Invalid, Unsupported, OSError, UnicodeDecodeError) as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
    return report


def run() -> dict:
    settings = json.loads((TESTS / "connection.json").read_text())

    def post(action: str, body: dict) -> dict:
        request = Request(
            settings["url"] + action,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + settings["token"]},
        )
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.load(response)

    admitted = post("/begin", {})
    if SUBMISSION.exists():
        try:
            record = run_case(
                read(SUBMISSION),
                LOGS / "native",
                llm_mode=admitted["llm_mode"],
                bindings=read_bindings(TESTS / "bindings.yaml"),
                bridge_url=admitted["bridge_url"],
                operation_url=admitted["operation_url"],
                operation_token=admitted["operation_token"],
                deadline_at=admitted["deadline_at"],
            )
        except Exception as error:  # Whatever happens, /finish must freeze the environment
            record = {"status": "error", "output": None, "error": {"type": type(error).__name__}}
    else:
        record = {"status": "missing_submission", "output": None}
    write_record_json(LOGS / "case.json", record)
    return post("/finish", {"record": record, "submission_sha256": submission_sha256()})


def harbor_reward(result: dict) -> str | None:
    """The reward Harbor reads for a hosted run; None writes no file, since not evaluated is not zero."""
    quality = result["quality"]
    if quality is None:
        # Without a quality the acceptance is the trial's only verdict, as in the fixture verifier.
        return None if result["acceptance"] is None else ("1" if result["acceptance"] else "0")
    return None if quality.get("normalized_reward") is None else str(quality["normalized_reward"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["admit", "run"])
    action = parser.parse_args(argv).action
    if os.environ.get("SAPI_HOSTED_ADMISSION") == "1":
        action = "admit"
    LOGS.mkdir(parents=True, exist_ok=True)
    report = admit(json.loads((TESTS / "admission.json").read_text())) if action == "admit" else run()
    (LOGS / "evaluation").mkdir(exist_ok=True)
    write_json(LOGS / "evaluation/report.json", report)
    if action == "admit":
        (LOGS / "reward.txt").write_text(("1" if report["passed"] else "0") + "\n")
    elif (reward := harbor_reward(report["result"])) is not None:
        (LOGS / "reward.txt").write_text(reward + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
