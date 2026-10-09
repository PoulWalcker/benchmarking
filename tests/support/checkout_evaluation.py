"""Explicit checkout domain modules for tests of retained behavior."""

from importlib import import_module
import sys
from types import ModuleType

from sapi_config_lab.paths import workspace_root

ROOT = workspace_root()
package = ModuleType("checkout_task")
package.__path__ = [str(ROOT / "tasks/checkout-recovery")]
sys.modules[package.__name__] = package
SCORING = import_module("checkout_task.evaluation.scoring")
CALIBRATION = import_module("checkout_task.evaluation.calibration")
SERVER = import_module("checkout_task.environment.server")
