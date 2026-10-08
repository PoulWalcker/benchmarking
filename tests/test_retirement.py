"""Retired production names cannot be selected; historical and test-only graphs remain outside discovery."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.benchmark import discover_benchmarks
from sapi_config_lab.coordinate.benchmark_discovery import select_benchmarks
from sapi_config_lab.coordinate.cli import main
from sapi_config_lab.paths import workspace_root

RETIRED = (
    "ticket-routing",
    "competitor-report",
    "revise-answer",
    "daily-digest",
    "dual-ledger-closeout",
    "support-review-packet",
    "bulletin-market-brief",
    "priority-support-brief",
    "crm-lead-qualification",
)


class RetirementTests(unittest.TestCase):
    def test_production_selection_is_exactly_two_and_default_is_invoice_only(self):
        root = workspace_root() / "benchmarks"
        self.assertEqual({item.name for item in discover_benchmarks(root)}, {"invoice-total", "checkout-recovery"})
        self.assertEqual({item.name for item in select_benchmarks(root)}, {"invoice-total"})
        for options, expected in (([], {"invoice-total", "checkout-recovery"}), (["--defaults"], {"invoice-total"})):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["benchmarks", *options]), 0)
            self.assertEqual({row["id"] for row in json.loads(output.getvalue())}, expected)

    def test_retired_names_refuse_compile_and_package_without_creating_output(self):
        root = workspace_root()
        config = root / "benchmarks/01-invoice-total/config.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            for name in RETIRED:
                with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaisesRegex(ValueError, "Unknown"):
                        select_benchmarks(root / "benchmarks", (name,))
                    output = Path(temporary) / name
                    self.assertEqual(main(["compile", str(config), "--scenario", name, "--output", str(output)]), 2)
                    self.assertEqual(main(["package-tasks", str(output), "--scenario", name]), 2)
                    self.assertIn("Unknown", errors.getvalue())
                    self.assertFalse(output.exists())
                self.assertFalse(list((root / "benchmarks").glob("[0-9][0-9]-" + name)))

    def test_build_compiles_only_retained_references(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["build", "--output-dir", temporary]), 0)
            self.assertEqual(
                {row["config"].split("-", 1)[1] for row in json.loads(output.getvalue())},
                {"invoice-total", "checkout-recovery"},
            )
            self.assertEqual(
                {path.name.split(".", 1)[0].split("-", 1)[1] for path in Path(temporary).glob("*.n8n.json")},
                {"invoice-total", "checkout-recovery"},
            )
