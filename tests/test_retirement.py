"""Retired production names cannot be selected; historical and test-only graphs remain outside discovery."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.coordinate.cli import main
from sapi_config_lab.coordinate.native_tasks import select_tasks
from sapi_config_lab.paths import workspace_root

RETIRED = (
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
    def test_production_selection_includes_retained_tasks_and_default_is_invoice_only(self):
        root = workspace_root() / "tasks"
        names = {path.parent.name for path in root.glob("*/task.toml")}
        self.assertEqual({item.name for item in select_tasks(root, sorted(names))}, names)
        self.assertEqual({item.name for item in select_tasks(root)}, {"invoice-total"})
        for options, expected in (
            ([], names),
            (["--defaults"], {"invoice-total"}),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["benchmarks", *options]), 0)
            self.assertEqual({row["id"] for row in json.loads(output.getvalue())}, expected)

    def test_retired_names_refuse_compile_without_creating_output(self):
        root = workspace_root()
        config = root / "tasks/invoice-total/solution/config.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            for name in RETIRED:
                with self.subTest(name=name), contextlib.redirect_stderr(io.StringIO()) as errors:
                    with self.assertRaisesRegex(ValueError, "Unknown"):
                        select_tasks(root / "tasks", (name,))
                    output = Path(temporary) / name
                    self.assertEqual(main(["compile", str(config), "--scenario", name, "--output", str(output)]), 2)
                    self.assertIn("Unknown", errors.getvalue())
                    self.assertFalse(output.exists())
                self.assertFalse(list((root / "tasks").glob(name)))

    def test_build_compiles_only_retained_references(self):
        root = workspace_root() / "tasks"
        names = {
            task.name for task in select_tasks(root, sorted(path.parent.name for path in root.glob("*/task.toml")))
        }
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["build", "--output-dir", temporary]), 0)
            self.assertEqual(
                {row["config"] for row in json.loads(output.getvalue())},
                names,
            )
            self.assertEqual(
                {path.name.split(".", 1)[0] for path in Path(temporary).glob("*.n8n.json")},
                names,
            )
