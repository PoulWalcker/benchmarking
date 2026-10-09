"""Install exact external source and dependency bytes only into the trusted image."""

import hashlib
import io
import json
from pathlib import Path
import sysconfig
from urllib.request import urlopen
import zipfile

ROOT = Path("/tests/payload")
WHEEL = "https://files.pythonhosted.org/packages/49/82/2755c7c982086f00d4dab85bc120ec35045a9fc2191893a6ce79afe94443/fastjsonschema-2.22.2-py3-none-any.whl"
WHEEL_SHA256 = "0fb3915616adac85ccfdd737d26be1089845d2019819505b42d39888458f74d4"


def fetch(url, expected):
    with urlopen(url, timeout=60) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000 or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("External dependency hash mismatch")
    return data


def main():
    pin = json.loads((ROOT / "provenance/autowfbench-source.json").read_text())
    base = pin["repository"].replace("https://github.com/", "https://raw.githubusercontent.com/")
    source = ROOT / "vendor/autowfbench"
    for name, expected in pin["files"].items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(base + "/" + pin["revision"] + "/" + name, expected))
    with zipfile.ZipFile(io.BytesIO(fetch(WHEEL, WHEEL_SHA256))) as archive:
        archive.extractall(sysconfig.get_paths()["purelib"])


if __name__ == "__main__":
    main()
