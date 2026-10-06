"""Malformed definitions fail at the public profile interface with field paths."""

import copy
from pathlib import Path
import tempfile
import unittest

from sapi_config_lab.paths import CATALOG, workspace_root
from sapi_config_lab import profile


class ProfileValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = profile.read(workspace_root() / "benchmarks/03-competitor-report/config.yaml")
        cls.bindings = profile.read_bindings(CATALOG)

    def test_invalid_shapes_report_the_offending_field(self):
        probes = [
            (("workflow", "id"), 1, "workflow.id"),
            (("workflow", "revision"), True, "workflow.revision"),
            (("workflow", "steps", 0, "uses"), [], "workflow.steps[0].uses"),
            (("workflow", "steps", 0, "actor"), {}, "workflow.steps[0].actor"),
            (("workflow", "steps", 0, "with"), None, "workflow.steps[0].with"),
            (("workflow", "steps", 0, "when"), None, "workflow.steps[0].when"),
            (("workflow", "dependencies"), None, "workflow.dependencies"),
            (("workflow", "dependencies", 0), [{}, "combine"], "workflow.dependencies[0][0]"),
            (("activation",), None, "activation"),
            (("activation", "workflow_ref"), [], "activation.workflow_ref"),
            (("execution",), [], "execution"),
            (("execution", "deadline_seconds"), True, "execution.deadline_seconds"),
            (("actors",), [], "actors"),
            (("actors", "product-sapi", "allowed_operations"), [[]], "actors.product-sapi.allowed_operations[0]"),
            (("workflow", "steps", 0, "with", "material", "ref"), [], "workflow.steps[0].with.material.ref"),
        ]
        for route, invalid, location in probes:
            config = copy.deepcopy(self.config)
            target = config
            for key in route[:-1]:
                target = target[key]
            target[route[-1]] = invalid
            with self.subTest(path=location), self.assertRaises(profile.Invalid) as caught:
                profile.validate(config, self.bindings)
            self.assertIn(location, str(caught.exception))

    def test_binding_schema_rejects_unhashable_required_and_type_members(self):
        for field, value in [("required", [{}]), ("type", [[]])]:
            bindings = copy.deepcopy(self.bindings)
            bindings["research.product"]["input_schema"][field] = value
            with self.subTest(field=field), self.assertRaises(profile.Invalid) as caught:
                profile.validate(self.config, bindings)
            self.assertIn("bindings.operations.research.product.input_schema", str(caught.exception))

    def test_non_json_yaml_values_and_recursive_aliases_are_rejected(self):
        for value in [float("nan"), float("inf"), b"bytes", {"set"}]:
            config = copy.deepcopy(self.config)
            config["workflow"]["inputs"]["invalid"] = value
            with self.subTest(value=value), self.assertRaises(profile.Invalid) as caught:
                profile.validate(config, self.bindings)
            self.assertIn("config.workflow.inputs.invalid", str(caught.exception))

    def test_yaml_syntax_and_non_string_keys_have_readable_locations(self):
        for source, message in [
            ("schema: [", "line"),
            ("? [a, b]\n: value\n", "mapping key"),
            ("value: 1\nvalue: 2", "Duplicate YAML key"),
        ]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bad.yaml"
                path.write_text(source)
                with self.subTest(source=source), self.assertRaises(profile.Invalid) as caught:
                    profile.read(path)
                self.assertIn(message, str(caught.exception))

    def test_extensions_validate_nested_fields_before_rejecting_capability(self):
        for filename, route, value, location in [
            (
                "04-refinement.yaml",
                ("execution", "refinement", "initial_state"),
                [],
                "execution.refinement.initial_state",
            ),
            ("05-scheduled-digest.yaml", ("lifecycle", "on_test_pass"), None, "lifecycle.on_test_pass"),
        ]:
            # Match the existing example names without coupling to editorial stems.
            path = next((workspace_root() / "benchmarks").glob(filename[:2] + "-*/config.yaml"))
            config = profile.read(path)
            target = config
            for key in route[:-1]:
                target = target[key]
            target[route[-1]] = value
            with self.subTest(path=location), self.assertRaises(profile.Invalid) as caught:
                profile.validate(config, self.bindings)
            self.assertIn(location, str(caught.exception))
