"""Explicit retained benchmark inputs for tests of generic mechanisms."""

from importlib import import_module
import json
import sys
from types import ModuleType

from sapi_config_lab.paths import workspace_root

DIRECTORY = workspace_root() / "tasks/invoice-total"
CATALOG = DIRECTORY / "bindings.yaml"
OPERATION_SOURCE = (DIRECTORY / "operations.js").read_text()


package = ModuleType("invoice_task")
package.__path__ = [str(DIRECTORY)]
sys.modules[package.__name__] = package
EVALUATOR = import_module("invoice_task.evaluation.evaluator")


def fixture():
    return EVALUATOR._fixture({})


def cases():
    return json.loads((DIRECTORY / "cases.json").read_text())


def card():
    return fixture().rubric
