"""The UI preparation interface never dispatches and binds one owned workflow."""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sapi_config_lab.coordinate.ui import main, open_workflow, prepare
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.paths import workspace_root


class FakeN8n:
    identity = "existing-container"

    def __init__(self):
        self.rows = {"protected": {"id": "protected", "name": "User workflow", "active": True}}
        self.imports = 0

    def workflows(self):
        return copy.deepcopy(self.rows)

    def import_new(self, path, *, existing):
        self.assert_unchanged(existing)
        row = json.loads(path.read_text())
        assert row["id"] not in self.rows and row["active"] is False
        row["createdAt"] = "native-added-metadata"
        self.rows[row["id"]] = row
        self.imports += 1
        return copy.deepcopy(row)

    def assert_unchanged(self, existing):
        assert self.rows == existing


class UiTests(unittest.TestCase):
    def test_malformed_config_fails_locally_and_view_ignores_wrapper_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.yaml"
            for raw in ("workflow: {}\n", "null\n", "workflow: [\n"):
                source.write_text(raw)
                with (
                    patch("sapi_config_lab.coordinate.ui.DockerUi") as docker,
                    patch("sapi_config_lab.coordinate.ui.wrapper_preference") as wrapper,
                    contextlib.redirect_stderr(io.StringIO()) as errors,
                ):
                    with self.assertRaises(SystemExit) as caught:
                        main(["open", str(source), "--wrapper-evidence", "absent.json", "--state-dir", temporary])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertNotIn("Traceback", errors.getvalue())
                    docker.assert_not_called()
                    wrapper.assert_not_called()

    def test_live_browser_opens_only_after_bridge_is_ready(self):
        adapter = FakeN8n()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch("sapi_config_lab.coordinate.ui.DockerUi", return_value=adapter),
                patch("sapi_config_lab.coordinate.ui.wrapper_preference", return_value=(Path("identity.json"), {})),
                patch("sapi_config_lab.coordinate.ui.webbrowser.open") as browser,
                patch("sapi_config_lab.coordinate.ui.serve") as bridge,
                contextlib.redirect_stdout(io.StringIO()),
            ):

                def start(*args, **kwargs):
                    browser.assert_not_called()
                    kwargs["on_ready"]()

                bridge.side_effect = start
                main(
                    [
                        "open",
                        str(workspace_root() / "benchmarks/04-revise-answer/config.yaml"),
                        "--live",
                        "--state-dir",
                        temporary,
                    ]
                )
                browser.assert_called_once()

    def test_open_reuses_post_import_identity_and_preserves_user_edits(self):
        adapter = FakeN8n()
        original = copy.deepcopy(adapter.rows)
        source = workspace_root() / "benchmarks/09-priority-support-brief/config.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            first = open_workflow(source, state, adapter=adapter)
            again = open_workflow(source, state, adapter=adapter)
            self.assertEqual(first["workflow_id"], again["workflow_id"])
            self.assertEqual(again["status"], "reused")
            self.assertEqual(adapter.imports, 1)
            self.assertFalse(first["executed"])
            self.assertFalse(list(state.rglob("budget.json")))
            self.assertFalse(list(state.rglob("bridge-audit.jsonl")))
            adapter.rows[first["workflow_id"]]["nodes"][0]["position"] = [999, 999]
            changed = copy.deepcopy(adapter.rows)
            edited = open_workflow(source, state, adapter=adapter)
            self.assertEqual(edited["status"], "reused_edited")
            self.assertEqual(edited["workflow_url"], first["workflow_url"])
            self.assertFalse(edited["matches_prepared"])
            self.assertEqual(adapter.imports, 1)
            self.assertEqual(adapter.rows, changed)
            fresh = open_workflow(source, state, adapter=adapter, new_copy=True)
            self.assertNotEqual(first["workflow_id"], fresh["workflow_id"])
            self.assertEqual(adapter.rows[first["workflow_id"]], changed[first["workflow_id"]])
            self.assertEqual(adapter.rows["protected"], original["protected"])
            del adapter.rows[fresh["workflow_id"]]
            with self.assertRaisesRegex(ValueError, "was removed"):
                open_workflow(source, state, adapter=adapter)
            self.assertEqual(adapter.imports, 2)

    def test_failed_import_cannot_be_silently_retried(self):
        adapter = FakeN8n()
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            source = workspace_root() / "benchmarks/01-invoice-total/config.yaml"
            with patch.object(adapter, "import_new", side_effect=ValueError("unknown import outcome")):
                with self.assertRaisesRegex(ValueError, "unknown import"):
                    open_workflow(source, state, adapter=adapter)
            with self.assertRaisesRegex(ValueError, "Previous import outcome is unknown"):
                open_workflow(source, state, adapter=adapter)
            self.assertEqual(adapter.imports, 0)

    def test_imports_eight_of_nine_inactive_graphs_and_defers_the_lifecycle_one(self):
        adapter = FakeN8n()
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with (
                patch("sapi_config_lab.coordinate.ui.DockerUi", return_value=adapter),
                patch("sapi_config_lab.coordinate.ui.serve") as bridge,
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(main(["open", "--all", "--no-browser", "--state-dir", temporary]), 0)
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(rows), 9)
            self.assertEqual(adapter.imports, 8)
            self.assertEqual(sum(row["status"] == "controller_required" for row in rows), 1)
            self.assertTrue(all(row["executed"] is False for row in rows))
            bridge.assert_not_called()
            self.assertFalse(list(Path(temporary).rglob("budget.json")))

    def test_live_open_creates_fresh_copy_with_exact_overlay_and_derived_cap(self):
        adapter = FakeN8n()
        source = workspace_root() / "benchmarks/04-revise-answer/config.yaml"
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (
                patch("sapi_config_lab.coordinate.ui.DockerUi", return_value=adapter),
                patch("sapi_config_lab.coordinate.ui.wrapper_preference", return_value=(Path("identity.json"), {})),
                patch("sapi_config_lab.coordinate.ui.serve") as bridge,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                for _ in range(2):
                    main(["open", str(source), "--live", "--no-browser", "--state-dir", str(state)])
            self.assertEqual(adapter.imports, 2)
            self.assertEqual(bridge.call_count, 2)
            self.assertEqual(bridge.call_args.kwargs["max_attempts"], 3)
            directory = bridge.call_args.args[0]
            prepared = json.loads((directory / "prepared.json").read_text())
            self.assertEqual(prepared["overlay"], {"execution.deadline_seconds": {"original": 120, "prepared": 600}})
            self.assertEqual((directory / "original.yaml").read_bytes(), source.read_bytes())
            import yaml

            original = yaml.safe_load(source.read_text())
            changed = yaml.safe_load((directory / "config.yaml").read_text())
            original["execution"]["deadline_seconds"] = 600
            self.assertEqual(changed, original)
            self.assertFalse((directory / "budget.json").exists())

    def test_missing_preparation_is_actionable_before_any_network_request(self):
        from sapi_config_lab.coordinate.ui import main

        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "ui-priority-01"
            errors = io.StringIO()
            with (
                patch("sapi_config_lab.coordinate.ui.wrapper_identity", return_value={"model": "gpt-6-astra"}),
                patch("sapi_config_lab.coordinate.ui.urlopen") as request,
                contextlib.redirect_stderr(errors),
            ):
                request.return_value.__enter__.return_value.status = 200
                with self.assertRaises(SystemExit) as caught:
                    main(
                        [
                            "serve",
                            str(missing),
                            "--max-attempts",
                            "4",
                            "--seconds",
                            "600",
                            "--wrapper-evidence",
                            "unused.json",
                        ]
                    )
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("ui open", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
            request.assert_not_called()

    def test_preparing_a_live_graph_freezes_exact_inputs_and_a_single_run_cap(self):
        source = workspace_root() / "benchmarks/09-priority-support-brief/config.yaml"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            prepared = prepare(source, output, host=HostConfig())
            self.assertEqual(prepared["max_attempts"], 4)
            self.assertEqual(
                prepared["operations"],
                {
                    "ticket.classify": 1,
                    "research.product": 1,
                    "research.marketing": 1,
                    "research.write": 1,
                },
            )
            self.assertEqual((output / "config.yaml").read_bytes(), source.read_bytes())
            workflow = json.loads((output / "workflow.json").read_text())
            self.assertFalse(workflow["active"])
            self.assertEqual(workflow["id"], prepared["workflow_id"])
            self.assertTrue(workflow["name"].startswith("Sapi lab / "))
            urls = {n["parameters"]["url"] for n in workflow["nodes"] if n["type"].endswith("httpRequest")}
            self.assertEqual(urls, {"http://host.docker.internal:18766/v1/agency/execute"})
            self.assertFalse((output / "bridge-audit.jsonl").exists())
            with self.assertRaises(FileExistsError):
                prepare(source, output, host=HostConfig())

    def test_admission_requires_explicit_cap_and_unmodified_files_and_cannot_resume(self):
        from sapi_config_lab.coordinate.ui import admit

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            prepare(workspace_root() / "benchmarks/09-priority-support-brief/config.yaml", output)
            with self.assertRaisesRegex(ValueError, "cap"):
                admit(output, max_attempts=5, seconds=600, model="gpt-6-astra")
            self.assertFalse((output / "budget.json").exists())
            original = (output / "workflow.json").read_bytes()
            (output / "workflow.json").write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "changed"):
                admit(output, max_attempts=4, seconds=600, model="gpt-6-astra")
            (output / "workflow.json").write_bytes(original)
            grant = admit(output, max_attempts=4, seconds=600, model="gpt-6-astra")
            self.assertEqual(grant["workflow_id"], json.loads(original)["id"])
            self.assertEqual(grant["max_attempts"], 4)
            with self.assertRaises(FileExistsError):
                admit(output, max_attempts=4, seconds=600, model="gpt-6-astra")
