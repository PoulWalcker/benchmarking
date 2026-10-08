"""Unpaid native refinement observations inside one Harbor-managed environment."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from sapi_config_lab.contracts import CompileOptions, RunBinding
from sapi_config_lab.coordinate.backend import N8nBackend
from sapi_config_lab.evidence import write_record_json
from sapi_config_lab.profile import read_bindings
from tests.support.refinement import ROOT, definition, verify_refinement


class Bridge(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.calls = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        server = self.server
        assert isinstance(server, Bridge)
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(request)
        previous = request["inputs"]["previous"]
        response = {
            "invocation_id": request["invocation_id"],
            "status": "completed",
            "output": {"value": 1 if previous is None else previous["value"] + 1},
        }
        raw = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    output = Path("/logs/verifier/refinement")
    output.mkdir()
    backend = N8nBackend(operation_source=(ROOT / "operations.js").read_text())
    bindings = read_bindings(ROOT / "bindings.yaml")
    server = Bridge()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    summaries = []
    try:
        for name, mode, target, ceiling, expected in (
            ("first", "live", 1, 10, 1),
            ("later", "live", 2, 10, 2),
            ("exhausted", "live", 2, 1, None),
            ("stub", "stub", 2, 10, 2),
        ):
            server.calls.clear()
            config = definition()
            config["workflow"]["inputs"].update(target=target, ceiling=ceiling)
            options = CompileOptions(mode, f"http://127.0.0.1:{server.server_port}" if mode == "live" else None)
            compiled = backend.compile(config, bindings, options)
            run = backend.execute(compiled, output / name, RunBinding())
            verdict = verify_refinement(config, run, mode=mode)
            count = expected or 3
            assert verdict["accepted_attempt"] == expected
            assert verdict["attempt_count"] == count
            assert len(verdict["calls"]) == len(server.calls) == (count if mode == "live" else 0)
            assert run["output"] == ({"value": expected} if expected else None)
            write_record_json(output / name / "config.json", config)
            write_record_json(output / name / "native.json", run)
            write_record_json(output / name / "bridge.json", server.calls)
            write_record_json(output / name / "verification.json", verdict)
            summaries.append(
                {
                    "name": name,
                    "accepted_attempt": expected,
                    "attempts": count,
                    "native_calls": len(verdict["calls"]),
                    "bridge_calls": len(server.calls),
                    "exhausted": verdict["exhausted"],
                    "output": run["output"],
                }
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    write_record_json(output / "summary.json", {"passed": True, "cases": summaries})
    Path("/logs/verifier/reward.txt").write_text("1\n")


if __name__ == "__main__":
    main()
