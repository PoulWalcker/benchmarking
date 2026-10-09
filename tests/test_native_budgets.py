"""Native world attempts and declared phase limits compose before dispatch."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.live import main as live
from sapi_config_lab.coordinate.live import runtime_grant_seconds
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.coordinate.runs import Run
from sapi_config_lab.evidence import sha256
from sapi_config_lab.paths import workspace_root
from tests.test_runs import FakeHost

TASKS = workspace_root() / "tasks"


class NativeBudgetTests(unittest.TestCase):
    def test_multiple_attempts_cannot_reuse_one_native_world(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))
            run = Run(root / "run", {}, {}, "test", native_tasks=selected)
            with self.assertRaisesRegex(ValueError, "one attempt"):
                run.harbor("job", selected[0], "oracle", attempts=2)

    def test_bridge_grant_uses_declared_native_phases(self):
        selected = select_tasks(workspace_root() / "tasks", ("checkout-recovery",))[0]
        self.assertGreaterEqual(runtime_grant_seconds(selected), 120)


def two_research_cases() -> dict:
    """A hypothetical cohort: V1 selects only Orion, so both live cases are named explicitly here."""
    cases = json.loads((TASKS / "research-report/cases.json").read_text())
    return {**cases, "live_cases": ["orion-clinics", "beacon-bookings"]}


class CohortBudgetTests(unittest.TestCase):
    """`live --preflight-only` admits a cohort only when every case's runtime cap and Judge cost fit."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        quiet = patch("sapi_config_lab.coordinate.runs.progress")
        quiet.start()
        self.addCleanup(quiet.stop)

    def preflight(self, max_calls: int, cases: dict[str, dict | None]) -> tuple[int, dict, FakeHost, Path]:
        host = FakeHost(self)
        output = self.root / f"run-{len(list(self.root.iterdir()))}"
        control = self.root / "control.json"
        control.write_text("{}")
        submissions = {
            name: {
                "path": TASKS / name / "solution/config.yaml",
                "sha256": sha256(TASKS / name / "solution/config.yaml"),
                **({"cases": selected} if selected else {}),
            }
            for name, selected in cases.items()
        }
        manifest = self.root / "selection.json"
        manifest.write_text("{}")

        def selection(_path, *, copy_to):
            copy_to.mkdir(parents=True)
            return submissions

        gate = {"oracle": {"trials": [{"task_name": name} for name in cases]}}
        with (
            patch("sapi_config_lab.coordinate.live.validate_control", return_value=gate),
            patch("sapi_config_lab.coordinate.live.load_selection", side_effect=selection),
            patch.object(Run, "use_native_tasks"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            arguments = ["--stub-report", str(control), "--submissions-manifest", str(manifest)]
            arguments += ["--max-calls", str(max_calls), "--report-dir", str(output), "--preflight-only"]
            arguments += ["--judge-model", "gpt-6.1-sol"]
            code = live(arguments)
        return code, json.loads((output / "report.json").read_text()), host, output

    def assert_refused(self, max_calls: int, cases: dict, needed: int) -> None:
        code, report, host, output = self.preflight(max_calls, cases)
        self.assertEqual(code, 1)
        self.assertIn(f"needs up to {needed} model calls; --max-calls allows {max_calls}", report["error"])
        self.assertEqual(host.ran, [])
        self.assertFalse((output / "ledger.json").exists())

    def assert_admitted(self, max_calls: int, cases: dict, runtime: dict, judge: dict) -> None:
        _code, report, _host, output = self.preflight(max_calls, cases)
        total = sum(runtime.values()) + sum(judge.values())
        self.assertEqual(report["budget"], {"max_calls": max_calls, "cases": runtime, "judge": judge, "total": total})
        self.assertNotIn("--max-calls", report.get("error", ""))
        # The ledger can reserve exactly the reported allocation per phase, never the whole --max-calls twice.
        ceilings = json.loads((output / "ledger.json").read_text())["ceilings"]
        self.assertEqual(
            ceilings, {"runtime": sum(runtime.values())} | ({"judge": sum(judge.values())} if judge else {})
        )

    def test_every_judged_case_adds_its_own_judge_call(self):
        self.assert_refused(3, {"research-report": None}, 4)
        self.assert_admitted(
            4, {"research-report": None}, {"research-report/orion-clinics": 3}, {"research-report/orion-clinics": 1}
        )
        self.assert_refused(7, {"research-report": two_research_cases()}, 8)
        both = {"research-report/orion-clinics": 3, "research-report/beacon-bookings": 3}
        self.assert_admitted(8, {"research-report": two_research_cases()}, both, dict.fromkeys(both, 1))

    def test_mixed_and_judge_free_cohorts_sum_each_case_once(self):
        mixed = {"research-report": two_research_cases(), "invoice-total": None, "checkout-recovery": None}
        invoice = {
            "invoice-total/original-38000": 0,
            "invoice-total/alternate-values-zero": 0,
            "invoice-total/maximum-safe-total": 0,
        }
        research = {"research-report/orion-clinics": 3, "research-report/beacon-bookings": 3}
        self.assert_refused(8, mixed, 9)
        self.assert_admitted(
            9,
            mixed,
            research | invoice | {"checkout-recovery/workflow": 0},
            dict.fromkeys(research, 1) | {"checkout-recovery/workflow": 1},
        )
        self.assert_admitted(0, {"invoice-total": None}, invoice, {})
