"""The new namespace reuses fixed durable admission; no model or Docker calls."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.experiments.generalization import GeneralizationSeries, TASKS, author, fresh_cases, stage
from sapi_config_lab.core.scenarios import SCENARIOS


class GeneralizationExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.manifest = patch(
            "sapi_config_lab.experiments.expansion.source_manifest", return_value={"frozen": "source"}
        )
        self.manifest.start()
        self.addCleanup(self.manifest.stop)

    def test_true_task_ids_fixed_caps_and_previous_final_live_gate(self):
        series = GeneralizationSeries(self.root / "series")
        self.assertEqual(series.scenarios, TASKS)
        self.assertEqual(series.ceilings, {"authoring": 4, "runtime": 14})
        self.assertTrue(set(TASKS).isdisjoint(SCENARIOS))
        for name in ("invoice-total", TASKS[1]):
            with self.assertRaises(ValueError):
                series.reserve(name, "authoring", "1", self.root / "report.json")
        for n in ("1", "2"):
            event = series.reserve(TASKS[0], "authoring", n, self.root / "author.json")
            series.finish(event, True)
        for case in ("base", "alternate"):
            event = series.reserve(TASKS[0], "runtime", case, self.root / "live.json")
            series.finish(event, True)
        with self.assertRaises(ValueError):
            series.reserve(TASKS[1], "authoring", "1", self.root / "next.json")
        (self.root / "live.json").write_text(
            json.dumps({"status": "failed", "scenario": TASKS[0], "source_unchanged": True})
        )
        with self.assertRaises(ValueError):
            series.reserve(TASKS[1], "authoring", "1", self.root / "next.json")
        (self.root / "live.json").write_text(
            json.dumps({"status": "passed", "scenario": TASKS[0], "source_unchanged": True})
        )
        series.reserve(TASKS[1], "authoring", "1", self.root / "next.json")
        with self.assertRaises(ValueError):
            series.reserve(TASKS[1], "authoring", "2", self.root / "next.json")

    def test_native_control_failure_stops_series_before_authoring_call(self):
        series = GeneralizationSeries(self.root / "series")
        with (
            patch(
                "sapi_config_lab.experiments.generalization.controls", side_effect=ValueError("native control failed")
            ),
            patch("sapi_config_lab.experiments.generalization.command") as dispatch,
            patch("sapi_config_lab.experiments.generalization.source_manifest", return_value={"frozen": "source"}),
        ):
            report = author(series, TASKS[0], image="lab:test", upstream="http://127.0.0.1:8765/run")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(series.data["events"], [])
        self.assertTrue((series.directory / "stopped.json").is_file())
        dispatch.assert_not_called()
        with self.assertRaises(ValueError):
            series.reserve(TASKS[0], "authoring", "1", self.root / "never.json")

    def test_packaging_keeps_reference_variants_out_of_generation_and_freezes_private_values(self):
        generated = self.root / "generated"
        stage(generated, TASKS[0], "lab:test")
        task = generated / TASKS[0]
        prompt = (task / "instruction.md").read_text()
        self.assertIn("at most one model-operation occurrence", prompt)
        self.assertNotIn("dependencies:", prompt.split("\n\nFORMAT")[0])
        self.assertFalse((task / "solution").exists())
        self.assertFalse((task / "environment/base.yaml").exists())
        self.assertIn("generalization_submission.py", (task / "tests/test.sh").read_text())
        controls = self.root / "controls"
        stage(controls, TASKS[0], "lab:test", controls=True)
        self.assertEqual(len(list(controls.glob("*/environment/base.yaml"))), 2)
        first, second = fresh_cases(TASKS[0]), fresh_cases(TASKS[0])
        self.assertNotEqual(first, second)
        self.assertEqual(first[TASKS[0]][1]["inputs"]["invoices"][0]["amount_minor"], 0)
        self.assertEqual(first[TASKS[0]][0]["anchors"], second[TASKS[0]][0]["anchors"])


if __name__ == "__main__":
    unittest.main()
