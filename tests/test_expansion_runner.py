"""Unpaid admission and private-fixture controls; no wrapper or Docker calls."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sapi_config_lab.coordinate.expansion import ExpansionSeries, RUNTIME_CAPS, fresh_case_overlay
from sapi_config_lab.coordinate.generate import main as generate
from sapi_config_lab.coordinate.live import main as live
from sapi_config_lab.coordinate.replay import read_json
from sapi_config_lab.coordinate.scenarios import all_cases


class ExpansionAdmissionTests(unittest.TestCase):
    def test_serial_series_exhausts_exact_eight_authoring_and_seventeen_runtime_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp))
            for scenario, cases in RUNTIME_CAPS.items():
                for attempt in ("1", "2"):
                    index = series.reserve(scenario, "authoring", attempt, Path(tmp) / "generation.json")
                    series.finish(index, True)
                for name in cases:
                    index = series.reserve(scenario, "runtime", name, Path(tmp) / (scenario + ".json"))
                    series.finish(index, True)
                import json

                (Path(tmp) / (scenario + ".json")).write_text(
                    json.dumps(
                        {
                            "status": "passed",
                            "scenario": scenario,
                            "source_unchanged": True,
                        }
                    )
                )
            persisted = read_json(series.path)
            self.assertEqual(sum(row["count"] for row in persisted["events"] if row["phase"] == "authoring"), 8)
            self.assertEqual(sum(row["count"] for row in persisted["events"] if row["phase"] == "runtime"), 17)
            with self.assertRaisesRegex(ValueError, "repeated"):
                series.reserve("priority-support-brief", "runtime", "normal-empty", Path(tmp) / "retry.json")

    def test_unknown_failure_and_out_of_order_attempts_cannot_reset_spent_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp))
            with self.assertRaisesRegex(ValueError, "Previous scenario"):
                series.reserve("support-review-packet", "authoring", "1", Path(tmp) / "out-of-order.json")
            index = series.reserve("dual-ledger-closeout", "authoring", "1", Path(tmp) / "first.json")
            reopened = ExpansionSeries(Path(tmp))
            with self.assertRaisesRegex(ValueError, "unknown outcome"):
                reopened.reserve("dual-ledger-closeout", "authoring", "2", Path(tmp) / "second.json")
            reopened.finish(index, False)
            with self.assertRaisesRegex(ValueError, "failure latch"):
                ExpansionSeries(Path(tmp)).reserve("dual-ledger-closeout", "authoring", "2", Path(tmp) / "third.json")
            self.assertEqual(len(read_json(series.path)["events"]), 1)

    def test_expansion_cli_cannot_start_without_named_caps_and_shared_series(self):
        with (
            contextlib.redirect_stderr(io.StringIO()) as errors,
            patch("sapi_config_lab.execute.host.subprocess.run") as outgoing,
        ):
            for args in (
                ["--scenario", "dual-ledger-closeout"],
                ["--scenario", "dual-ledger-closeout", "--attempts", "2"],
                ["--scenario", "unknown", "--attempts", "2"],
            ):
                with self.assertRaises(SystemExit) as caught:
                    generate(args)
                self.assertEqual(caught.exception.code, 2)
            outgoing.assert_not_called()
        with (
            contextlib.redirect_stderr(errors),
            patch("sapi_config_lab.execute.host.subprocess.run") as outgoing,
        ):
            with self.assertRaises(SystemExit) as caught:
                live(["--scenario", "dual-ledger-closeout", "--stub-report", "/unused.json"])
            self.assertEqual(caught.exception.code, 2)
            outgoing.assert_not_called()
        self.assertNotIn("Traceback", errors.getvalue())

    def test_private_overlay_changes_values_without_changing_acceptance_contracts(self):
        canonical = all_cases()
        for scenario in RUNTIME_CAPS:
            first = fresh_case_overlay(scenario)[scenario]
            second = fresh_case_overlay(scenario)[scenario]
            self.assertNotEqual(first["positive"][0]["inputs"], second["positive"][0]["inputs"])
            for kind in ("positive", "negative"):
                for public, private in zip(canonical[scenario][kind], first[kind]):
                    self.assertEqual(
                        {k: v for k, v in public.items() if k != "inputs"},
                        {k: v for k, v in private.items() if k != "inputs"},
                    )
            self.assertEqual(first["live_cases"], list(RUNTIME_CAPS[scenario]))
        dual = fresh_case_overlay("dual-ledger-closeout")["dual-ledger-closeout"]
        duplicate = next(row for row in dual["negative"] if row["name"] == "domestic-duplicate")["inputs"][
            "domestic_invoices"
        ]
        self.assertEqual(duplicate[0]["id"], duplicate[1]["id"])
        overflow = next(row for row in dual["negative"] if row["name"] == "export-overflow")["inputs"][
            "export_invoices"
        ]
        self.assertEqual([row["amount_minor"] for row in overflow], [9007199254740991, 1])
        priority = fresh_case_overlay("priority-support-brief")["priority-support-brief"]
        normal = next(row["inputs"] for row in priority["positive"] if row["name"] == "normal-empty")
        self.assertEqual(
            (normal["product_material"], normal["marketing_material"], normal["ticket"]["days_overdue"]), ("", "", 2)
        )
        reply = fresh_case_overlay("support-review-packet")["support-review-packet"]
        for case in reply["positive"]:
            inputs = case["inputs"]
            if case["name"] == "wrong-order-id":
                self.assertNotIn(inputs["required_order_id"], inputs["ticket"]["text"])
            else:
                self.assertIn(inputs["required_order_id"], inputs["ticket"]["text"])
        bulletin = fresh_case_overlay("bulletin-market-brief")["bulletin-market-brief"]
        malformed = next(row for row in bulletin["negative"] if row["name"] == "malformed-article-title")
        self.assertEqual(malformed["inputs"]["articles"][0]["title"], "")

    def test_changed_ceilings_or_spent_counts_fail_closed(self):
        import json

        for field, value in [("ceilings", {"authoring": 80, "runtime": 170}), ("count", 0), ("name", "2")]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                series = ExpansionSeries(Path(tmp))
                series.reserve("dual-ledger-closeout", "authoring", "1", Path(tmp) / "first.json")
                ledger = read_json(series.path)
                if field == "ceilings":
                    ledger[field] = value
                else:
                    ledger["events"][0][field] = value
                series.path.write_text(json.dumps(ledger))
                with self.assertRaises(ValueError):
                    ExpansionSeries(Path(tmp))

    def test_concurrent_reservations_admit_only_one_attempt(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            first, second = ExpansionSeries(Path(tmp)), ExpansionSeries(Path(tmp))

            def reserve(series):
                try:
                    return series.reserve("dual-ledger-closeout", "authoring", "1", Path(tmp) / "attempt.json")
                except ValueError:
                    return None

            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(reserve, [first, second]))
            self.assertEqual(results.count(0), 1)
            self.assertEqual(results.count(None), 1)
            self.assertEqual(len(read_json(first.path)["events"]), 1)

    def test_two_distinct_generated_answers_keep_independent_stub_byte_identities(self):
        from sapi_config_lab.coordinate.live import check_generated_stub_gates
        from sapi_config_lab.evidence import sha256

        with tempfile.TemporaryDirectory() as tmp:
            trials = []
            for number, content in enumerate(("first answer", "distinct second answer")):
                directory = Path(tmp) / str(number)
                (directory / "agent").mkdir(parents=True)
                (directory / "verifier").mkdir()
                own = directory / "agent/submission.yaml"
                own.write_text(content)
                (directory / "verifier/submission.yaml").write_text(content)
                trials.append(
                    {
                        "scenario": "dual-ledger-closeout",
                        "result_path": str(directory / "result.json"),
                        "rewards": {"reward": 1.0},
                        "exception": None,
                        "acceptance": {
                            "passed": True,
                            "scenario": "dual-ledger-closeout",
                            "mode": "stub",
                            "submission_sha256": sha256(own),
                            "cases": [{"passed": True}],
                        },
                    }
                )
            check_generated_stub_gates(trials, "dual-ledger-closeout", 1)
            (Path(tmp) / "1/verifier/submission.yaml").write_text("substituted first answer")
            with self.assertRaisesRegex(ValueError, "submission hash"):
                check_generated_stub_gates(trials, "dual-ledger-closeout", 1)

    def test_malformed_partial_trial_still_produces_a_failed_generation_report(self):
        from sapi_config_lab.coordinate.generate import finalize_generation

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report"
            staging = Path(tmp) / "staging"
            output.mkdir()
            directory = staging / "jobs/generated/trial"
            directory.mkdir(parents=True)
            (directory / "result.json").write_text("{partial")
            report = {"status": "failed", "limitations": [], "error": "TimeoutExpired", "trials": []}
            with contextlib.redirect_stdout(io.StringIO()):
                finalize_generation(report, output, staging, {}, ("dual-ledger-closeout",), 2)
            saved = read_json(output / "report.json")
            self.assertEqual(saved["status"], "failed")
            self.assertFalse(saved["experiment_completed"])
            self.assertTrue(any(error["stage"] == "trial_summary" for error in saved["collection_errors"]))
            self.assertEqual((output / "jobs/generated/trial/result.json").read_text(), "{partial")

    def test_failed_audit_aggregation_still_finalizes_the_live_report(self):
        from types import SimpleNamespace
        from sapi_config_lab.coordinate.live import run_expansion

        original_write = Path.write_text

        def fail_combined_audit(path, *args, **kwargs):
            if path.name == "bridge-audit.jsonl":
                raise OSError("audit destination unavailable")
            return original_write(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "live"
            args = SimpleNamespace(
                scenario="dual-ledger-closeout",
                report_dir=output,
                preflight_only=False,
                series_dir=Path(tmp) / "series",
            )
            with (
                contextlib.redirect_stdout(io.StringIO()),
                patch("sapi_config_lab.coordinate.live.ExpansionSeries", side_effect=ValueError("preflight failure")),
                patch.object(Path, "write_text", fail_combined_audit),
            ):
                self.assertEqual(run_expansion(args), 1)
            saved = read_json(output / "report.json")
            self.assertEqual(saved["status"], "failed")
            self.assertTrue(any(row["stage"] == "bridge_audit_aggregation" for row in saved["collection_errors"]))

    def test_final_live_collection_failure_blocks_the_next_scenario(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp))
            report = Path(tmp) / "live.json"
            for phase, names in [("authoring", ("1", "2")), ("runtime", ("base-ledgers", "alternate-ledgers"))]:
                for name in names:
                    reservation = series.reserve("dual-ledger-closeout", phase, name, report)
                    series.finish(reservation, True)
            report.write_text(
                json.dumps({"status": "failed", "scenario": "dual-ledger-closeout", "source_unchanged": True})
            )
            with self.assertRaisesRegex(ValueError, "final live gate failed"):
                ExpansionSeries(Path(tmp)).reserve("support-review-packet", "authoring", "1", Path(tmp) / "next.json")
            self.assertEqual(len(read_json(series.path)["events"]), 4)

    def test_selected_series_admits_only_its_four_authoring_and_thirteen_runtime_attempts(self):
        import json

        selected = ("bulletin-market-brief", "priority-support-brief")
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp), scenarios=selected)
            self.assertEqual(read_json(series.path)["scenarios"], list(selected))
            self.assertEqual(read_json(series.path)["ceilings"], {"authoring": 4, "runtime": 13})
            self.assertEqual(read_json(series.path)["events"], [])
            with self.assertRaisesRegex(ValueError, "not selected"):
                series.reserve("dual-ledger-closeout", "authoring", "1", Path(tmp) / "unselected.json")
            with self.assertRaisesRegex(ValueError, "Previous scenario"):
                series.reserve(selected[1], "authoring", "1", Path(tmp) / "out-of-order.json")
            for scenario in selected:
                for number in ("1", "2"):
                    series.finish(series.reserve(scenario, "authoring", number, Path(tmp) / "generation.json"), True)
                report = Path(tmp) / (scenario + ".json")
                for name in RUNTIME_CAPS[scenario]:
                    series.finish(series.reserve(scenario, "runtime", name, report), True)
                report.write_text(json.dumps({"status": "passed", "scenario": scenario, "source_unchanged": True}))
            events = read_json(series.path)["events"]
            self.assertEqual({event["scenario"] for event in events}, set(selected))
            self.assertEqual(sum(event["count"] for event in events if event["phase"] == "authoring"), 4)
            self.assertEqual(sum(event["count"] for event in events if event["phase"] == "runtime"), 13)
            for scenario, phase, name in [(selected[0], "authoring", "3"), (selected[1], "runtime", "normal-empty")]:
                with self.subTest(phase=phase), self.assertRaises(ValueError):
                    series.reserve(scenario, phase, name, Path(tmp) / "after-complete.json")
            self.assertEqual(read_json(series.path)["events"], events)

    def test_subset_identity_and_derived_budget_are_frozen_and_old_ledgers_are_not_migrated(self):
        import json

        selected = ("bulletin-market-brief", "priority-support-brief")
        for invalid in ((), ("unknown",), (selected[0], selected[0]), selected[::-1]):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp) / "not-created"
                with self.assertRaises(ValueError):
                    ExpansionSeries(directory, scenarios=invalid)
                self.assertFalse(directory.exists())
        mutations = [
            lambda ledger: ledger.update(scenarios=list(selected[::-1])),
            lambda ledger: ledger.update(scenarios=[selected[0]]),
            lambda ledger: ledger.update(ceilings={"authoring": 8, "runtime": 17}),
            lambda ledger: ledger.update(ceilings={"authoring": 4.0, "runtime": 13}),
            lambda ledger: ledger.update(schema="sapi-lab-expansion-series/v1"),
        ]
        for mutate in mutations:
            with tempfile.TemporaryDirectory() as tmp:
                series = ExpansionSeries(Path(tmp), scenarios=selected)
                ledger = read_json(series.path)
                mutate(ledger)
                series.path.write_text(json.dumps(ledger))
                before = series.path.read_bytes()
                with self.assertRaises(ValueError):
                    ExpansionSeries(Path(tmp), scenarios=selected)
                with self.assertRaises(ValueError):
                    series.reserve(selected[0], "authoring", "1", Path(tmp) / "rejected.json")
                self.assertEqual(series.path.read_bytes(), before)
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp), scenarios=selected)
            for changed in (None, (selected[0],)):
                with self.assertRaisesRegex(ValueError, "selection changed"):
                    ExpansionSeries(Path(tmp), scenarios=changed)
            with patch("sapi_config_lab.coordinate.expansion.source_manifest", return_value={"changed.py": "changed"}):
                with self.assertRaisesRegex(ValueError, "sources changed"):
                    series.reserve(selected[0], "authoring", "1", Path(tmp) / "drift.json")
            self.assertEqual(read_json(series.path)["events"], [])

    def test_subset_keeps_unknown_failure_and_previous_final_report_gates(self):
        import json

        selected = ("bulletin-market-brief", "priority-support-brief")
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp), scenarios=selected)
            report = Path(tmp) / "live.json"
            first = series.reserve(selected[0], "authoring", "1", report)
            with self.assertRaisesRegex(ValueError, "unknown outcome"):
                ExpansionSeries(Path(tmp), scenarios=selected).reserve(selected[0], "authoring", "2", report)
            series.finish(first, False)
            with self.assertRaisesRegex(ValueError, "failure latch"):
                ExpansionSeries(Path(tmp), scenarios=selected).reserve(selected[0], "authoring", "2", report)
        with tempfile.TemporaryDirectory() as tmp:
            series = ExpansionSeries(Path(tmp), scenarios=selected)
            report = Path(tmp) / "live.json"
            for phase, names in [("authoring", ("1", "2")), ("runtime", ("base-bulletins", "alternate-bulletins"))]:
                for name in names:
                    series.finish(series.reserve(selected[0], phase, name, report), True)
            with self.assertRaisesRegex(ValueError, "no final live report"):
                series.reserve(selected[1], "authoring", "1", Path(tmp) / "next.json")
            report.write_text(json.dumps({"status": "failed", "scenario": selected[0], "source_unchanged": True}))
            with self.assertRaisesRegex(ValueError, "final live gate failed"):
                series.reserve(selected[1], "authoring", "1", Path(tmp) / "next.json")
            self.assertEqual(len(read_json(series.path)["events"]), 4)

    def test_cli_passes_canonical_subset_and_rejects_bad_selection_before_any_runtime(self):
        selected = ("bulletin-market-brief", "priority-support-brief")
        flags = [arg for name in selected for arg in ("--series-scenario", name)]
        with patch("sapi_config_lab.coordinate.live.run_expansion", return_value=0) as launch:
            self.assertEqual(
                live(
                    [
                        "--stub-report",
                        "/unused.json",
                        "--submissions-manifest",
                        "/selection.json",
                        "--scenario",
                        selected[0],
                        "--series-dir",
                        "/new-series",
                        *flags,
                    ]
                ),
                0,
            )
            self.assertEqual(launch.call_args.args[0].series_scenarios, selected)
        for entry in (("unknown",), selected[::-1], (selected[0], selected[0]), ("dual-ledger-closeout",)):
            flags = [arg for name in entry for arg in ("--series-scenario", name)]
            with (
                self.subTest(entry=entry),
                contextlib.redirect_stderr(io.StringIO()),
                patch("sapi_config_lab.execute.host.subprocess.run") as outgoing,
            ):
                with self.assertRaises(SystemExit) as caught:
                    generate(["--scenario", selected[0], "--attempts", "2", "--series-dir", "/unused", *flags])
                self.assertEqual(caught.exception.code, 2)
                outgoing.assert_not_called()
            with (
                contextlib.redirect_stderr(io.StringIO()),
                patch("sapi_config_lab.coordinate.live.run_expansion") as launch,
            ):
                with self.assertRaises(SystemExit) as caught:
                    live(
                        [
                            "--stub-report",
                            "/unused.json",
                            "--submissions-manifest",
                            "/selection.json",
                            "--scenario",
                            selected[0],
                            "--series-dir",
                            "/unused",
                            *flags,
                        ]
                    )
                self.assertEqual(caught.exception.code, 2)
                launch.assert_not_called()

    def test_both_runner_reports_retain_selected_cohort_and_derived_series_caps(self):
        selected = ("bulletin-market-brief", "priority-support-brief")
        flags = [arg for name in selected for arg in ("--series-scenario", name)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                patch(
                    "sapi_config_lab.execute.host.subprocess.run",
                    side_effect=RuntimeError("unpaid control unavailable"),
                ),
            ):
                self.assertEqual(
                    generate(
                        [
                            "--scenario",
                            selected[0],
                            "--attempts",
                            "2",
                            "--series-dir",
                            str(root / "series"),
                            "--report-dir",
                            str(root / "generation"),
                            *flags,
                        ]
                    ),
                    1,
                )
            with (
                contextlib.redirect_stdout(io.StringIO()),
                patch(
                    "sapi_config_lab.coordinate.live.checked_harbor",
                    side_effect=RuntimeError("unpaid preflight unavailable"),
                ),
            ):
                self.assertEqual(
                    live(
                        [
                            "--scenario",
                            selected[0],
                            "--stub-report",
                            "/unused.json",
                            "--submissions-manifest",
                            "/unused-selection.json",
                            "--series-dir",
                            str(root / "series"),
                            "--report-dir",
                            str(root / "live"),
                            *flags,
                        ]
                    ),
                    1,
                )
            for name in ("generation", "live"):
                report = read_json(root / name / "report.json")
                self.assertEqual(report["series_scenarios"], list(selected))
                self.assertEqual(report["series_ceilings"], {"authoring": 4, "runtime": 13})
            self.assertEqual(read_json(root / "live/report.json")["budget"]["series_max_attempts"], 13)
            self.assertEqual(read_json(root / "series/series.json")["events"], [])
