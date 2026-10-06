"""Trusted Harbor verifier: run frozen YAML through the common real n8n backend."""

from pathlib import Path
import json
from urllib.request import Request, urlopen

from sapi_config_lab.evidence import write_record_json
from sapi_config_lab.coordinate.cases import run_case
from sapi_config_lab.profile import read, read_bindings


def main():
    settings = json.loads(Path("/tests/connection.json").read_text())

    def post(action, body):
        req = Request(
            settings["url"] + action,
            json.dumps(body).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + settings["token"]},
        )
        with urlopen(req, timeout=240) as response:
            return json.load(response)

    admitted = post("/begin", {})
    output = Path("/logs/verifier")
    output.mkdir(parents=True, exist_ok=True)
    candidate = Path("/app/submission/config.yaml")
    if candidate.exists():
        try:
            record = run_case(
                read(candidate),
                output / "native",
                llm_mode=admitted["llm_mode"],
                bindings=read_bindings("/tests/bindings.yaml"),
                bridge_url=admitted["bridge_url"],
                operation_url=admitted["operation_url"],
                operation_token=admitted["operation_token"],
                deadline_at=admitted["deadline_at"],
            )
        except Exception as error:
            record = {"status": "error", "output": None, "error": {"type": type(error).__name__}}
    else:
        record = {"status": "missing_submission", "output": None}
    write_record_json(output / "case.json", record)
    result = post("/finish", {"record": record})
    write_record_json(output / "evaluation.json", result)
    if result.get("normalized_reward") is not None:
        (output / "reward.txt").write_text(str(result["normalized_reward"]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
