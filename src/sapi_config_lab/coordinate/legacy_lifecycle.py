"""Explicit digest composition for legacy command entrypoints until benchmark retirement."""

from sapi_config_lab.coordinate.lifecycle import main as lifecycle_main
from sapi_config_lab.evaluate.operational import digest_acceptance


def main(argv: list[str] | None = None) -> int:
    return lifecycle_main(argv, verifier=digest_acceptance)
