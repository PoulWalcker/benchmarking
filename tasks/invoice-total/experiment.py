"""Invoice's exact authoring prompts and trusted experiment composition."""

import json
from pathlib import Path
import sys

import yaml

from sapi_config_lab.profile import read

ROOT = Path(__file__).resolve().parent


def authoring(catalog: str) -> dict:
    if catalog not in {"full", "scenario"}:
        raise ValueError("Unknown invoice catalog")
    text = (ROOT / "bindings.yaml").read_text()
    if catalog == "scenario":
        used = {step["uses"] for step in read(ROOT / "solution/config.yaml")["workflow"]["steps"]}
        kept, section, keep = [], "", True
        for line in text.splitlines(keepends=True):
            if line[:1].isalpha():
                section, keep = line.split(":", 1)[0], True
            elif section == "operations" and line.startswith("  ") and line[2:3].isalpha():
                keep = line.strip().removesuffix(":") in used
            if keep:
                kept.append(line)
        reduced = "".join(kept)
        full = yaml.safe_load(text)
        if yaml.safe_load(reduced) != {
            **full,
            "operations": {k: v for k, v in full["operations"].items() if k in used},
        }:
            raise ValueError("Catalog reduction changed operation declarations")
        text = reduced
    prompt = (
        "TASK\n"
        + (ROOT / "task.md").read_text().removesuffix("\n")
        + "\n\nFORMAT\n"
        + (ROOT.parent.parent / "generation/FORMAT.md").read_text()
        + "\n\nPROFILE\n"
        + (ROOT.parent.parent / "generation/PROFILE.md").read_text()
        + "\n\nOPERATION CATALOG\n"
        + text
    )
    return {"prompt": prompt, "catalog": text, "cases": json.loads((ROOT / "cases.json").read_text())}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    if request["action"] != "prompt":
        raise ValueError("Unknown invoice experiment action")
    print(json.dumps(authoring(request["catalog"])))
