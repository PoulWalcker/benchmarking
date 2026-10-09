"""Checkout's exact authoring prompt and trusted experiment composition."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if request["action"] != "prompt" or request["catalog"] != "full":
        raise ValueError("Unknown checkout experiment action or catalog")
    print(json.dumps({"prompt": (ROOT / "authoring-prompt.txt").read_text()}))
