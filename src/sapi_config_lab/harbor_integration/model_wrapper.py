"""Host wrapper transport, configuration and inspected dispatch identity."""

import argparse
from http.server import HTTPServer
import json
from pathlib import Path, PurePath
from urllib.request import Request

import yaml

from sapi_config_lab.evidence import sha256
from sapi_config_lab.execute.agency import WRAPPER_TIMEOUT_SECONDS, make_handler, strict_json
from sapi_config_lab.execute.host import HostConfig
from sapi_config_lab.net import urlopen


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return strict_json(path.read_bytes())


def request_wrapper(upstream, prompt, timeout, maximum):
    """Send one bounded request without transport retries or redirects."""
    request = Request(upstream, json.dumps({"prompt": prompt}).encode(), {"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return response.read(maximum + 1)


SCHEMA = "sapi-lab-wrapper-identity/v1"


def parse_wrapper_files(values: list[str] | None) -> dict[str, Path]:
    """`--wrapper-file NAME=PATH` → {NAME: PATH}."""
    locations: dict[str, Path] = {}
    for value in values or []:
        name, _, path = value.partition("=")
        if not name or not path or name in locations:
            raise ValueError("--wrapper-file takes NAME=PATH, once per inspected file")
        locations[name] = Path(path)
    return locations


def wrapper_identity(path: Path, endpoint: str, model: str, locations: dict[str, Path] | None = None) -> dict:
    """Fail closed unless every inspected file is byte-identical here.

    A file's logical name is its recorded `name`, or the basename of its recorded
    `path`. It is read from `locations[name]` when given, else from the recorded path,
    so a run moved to another machine names its local copies instead of weakening the hash.
    """
    evidence = read_json(path)
    require(
        evidence.get("schema") == SCHEMA and evidence.get("endpoint") == endpoint, "Wrapper identity endpoint mismatch"
    )
    require(
        evidence.get("dispatch") == "codex-exec"
        and evidence.get("response_substitution") is False
        and evidence.get("wrapper_retries") == 0
        and evidence.get("model") == model,
        "Wrapper dispatch inspection is missing or incompatible",
    )
    require(
        evidence.get("provider_internal_retries") in ("unknown", "none", "observed"),
        "Missing lower-layer retry limitation",
    )
    files = evidence.get("files")
    require(isinstance(files, list) and len(files) >= 1, "Missing private wrapper source identity")
    names = [row.get("name") or PurePath(row.get("path") or "").name for row in files]
    require(all(names) and len(set(names)) == len(names), "Duplicate or unnamed wrapper source identity")
    locations = locations or {}
    require(set(locations) <= set(names), "--wrapper-file names a file the identity does not record")
    for name, row in zip(names, files, strict=True):
        local = locations.get(name) or (Path(row["path"]) if row.get("path") else None)
        require(local is not None, f"No local path for wrapper file {name}; pass --wrapper-file {name}=PATH")
        assert local is not None
        require(local.is_file() and sha256(local) == row["sha256"], f"Wrapper/config identity changed: {name}")
    return evidence


def main():
    host = HostConfig.from_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=host.listen_host, help="Bind address (SAPI_LISTEN_HOST)")
    parser.add_argument("--port", type=int, default=host.bridge_port)
    parser.add_argument("--upstream", default=host.wrapper_url)
    parser.add_argument("--timeout", type=int, default=WRAPPER_TIMEOUT_SECONDS)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--reject-tool-use", action="store_true", help="Fail a call whose wrapper reports tool use")
    args = parser.parse_args()
    catalog = yaml.safe_load(args.bindings.read_text())["operations"]
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    budget = strict_json(args.budget.read_bytes()) if args.budget else None
    handler = make_handler(
        catalog, args.upstream, args.timeout, args.audit, budget, args.reject_tool_use, transport=request_wrapper
    )
    server = HTTPServer((args.host, args.port), handler)
    print(f"Catalog adapter listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
