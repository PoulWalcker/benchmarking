"""Trusted fault injector copied only into the opt-in Docker test image."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

OUTPUT = Path("/logs/verifier")


def save(name, value):
    path = OUTPUT / name
    with path.open("x") as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())


def post(route, body, token):
    request = Request(
        "http://simulator:8000" + route,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def simulator(mode):
    from payload.environment import server

    original = server.World

    class InjectedWorld(original):
        def __init__(self, world, definition, **kwargs):
            if mode in {"deadline", "finish-race", "terminal-race"}:
                definition = {**definition, "limits": {**definition["limits"], "wall_clock_seconds": 0.5}}
            super().__init__(world, definition, **kwargs)
            self.fault_barrier = threading.Barrier(2)
            if mode == "finish-race":
                finalize = self.world.finalize

                def slow_finalize():
                    time.sleep(1)
                    return finalize()

                self.world.finalize = slow_finalize

        def _deadline(self):
            if mode == "terminal-race":
                self.fault_barrier.wait(timeout=5)
                return original.snapshot(self)
            return super()._deadline()

        def snapshot(self):
            if mode == "terminal-race" and self._snapshot is None:
                self.fault_barrier.wait(timeout=5)
            return super().snapshot()

    server.World = InjectedWorld
    server.main()


def attempt(mode):
    from payload.environment import hooks

    from sapi_config_lab.coordinate import benchmark_worker

    if mode == "evaluator-failure":
        from payload.evaluation import evaluator

        def fail(evidence, _options):
            save(
                "before-evaluator.json",
                {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence.iterdir() if p.is_file()},
            )
            raise RuntimeError("injected evaluator failure after immutable observations")

        evaluator.evaluate = fail
        return benchmark_worker.main()
    context = {
        "output": OUTPUT,
        "evidence": OUTPUT / "evidence",
        "submission": Path("/submission/config.yaml"),
        "options": {"mode": "stub"},
        "record": {"status": "unknown", "output": None},
    }
    if mode == "isolation-freshness":
        preflight = subprocess.check_output([sys.executable, "/tests/isolation_preflight.py"], text=True)
        save("preflight.json", [json.loads(line) for line in preflight.splitlines()])
    binding = hooks.prepare(context)
    credentials = json.loads(Path("/run/checkout/credentials.json").read_text())
    first = post(
        "/tools", {"operation": "source.read", "arguments": {}, "operation_id": "first"}, binding.operation_token
    )
    assert first["ok"], first
    save("phase.json", {"phase": "after-first-receipt", "pid": os.getpid(), "mode": mode})
    if mode in {"worker-death", "hard-timeout", "cancellation", "simulator-death"}:
        while not (OUTPUT / "release").exists():
            time.sleep(0.05)
        if mode == "worker-death":
            os.kill(os.getpid(), signal.SIGKILL)
    if mode == "deadline":
        time.sleep(0.8)
    if mode == "isolation-freshness":
        refused = []
        for route in ("/prepare", "/snapshot"):
            try:
                post(route, {}, binding.operation_token)
            except HTTPError as error:
                refused.append(error.code)
                error.close()
        assert refused == [401, 401], refused
        repeated = post(
            "/tools", {"operation": "source.read", "arguments": {}, "operation_id": "first"}, binding.operation_token
        )
        assert repeated == first
        mutation = {
            "operation": "checkout.patch",
            "arguments": {"old": "charge_card(currency, amount)", "new": "charge_card(amount, currency)"},
            "operation_id": "one-patch",
        }
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: post("/tools", mutation, binding.operation_token), range(4)))
        assert receipts[0]["ok"] and receipts == [receipts[0]] * 4
        save(
            "isolation.json",
            {
                "admin_denied": refused,
                "receipt_replayed": True,
                "credential_hashes": {
                    key: hashlib.sha256(value.encode()).hexdigest() for key, value in credentials.items()
                },
            },
        )
    trial = hooks.snapshot(context)
    save("observed-trial.json", trial)
    if mode == "isolation-freshness":
        frozen = json.loads((context["evidence"] / "environment-evidence.json").read_text())
        assert frozen["tool_calls"] == 2 and len(frozen["final"]["patches"]) == 1
        save(
            "freshness.json",
            {
                "initial_sha256": hashlib.sha256(json.dumps(frozen["initial"], sort_keys=True).encode()).hexdigest(),
                "run_id": trial["run_id"],
                "tool_calls": 2,
                "patches": 1,
            },
        )
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in context["evidence"].iterdir() if p.is_file()}
    context["record"] = {"status": "success", "output": {"final_answer": "late", "incident_summary": "late"}}
    assert hooks.snapshot(context) == trial
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in context["evidence"].iterdir() if p.is_file()
    }
    assert trial["terminal_completion"] is False
    for path in OUTPUT.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert all(value.encode() not in data for value in credentials.values()), path
    save("probe-proof.json", {"immutable_after_late_success": True, "credentials_absent": True})
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "simulator":
        simulator(sys.argv[2])
    else:
        raise SystemExit(attempt(sys.argv[1]))
