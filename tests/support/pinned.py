"""The pinned upstream checkout the hosted-scenario tests run against, when it has been fetched."""

import importlib.util

from sapi_config_lab.pinned_source import pinned_source

SOURCE = pinned_source("autowfbench")
AVAILABLE = (SOURCE.root / "autowfbench/__main__.py").is_file() and importlib.util.find_spec(
    "fastjsonschema"
) is not None
