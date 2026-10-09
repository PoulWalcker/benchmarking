"""Plan, observe and evaluate in one call, with an injected runner.

Native task composition calls these as separate stages. Tests
compose them here so a fake runner can stand in for n8n; the verifier itself
never receives the runner.
"""

from pathlib import Path

from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.evidence import write_record_json


def verify_with_runner(
    verifier,
    scenario,
    config_path,
    run,
    mode="stub",
    selected_case=None,
    *,
    runner,
    cases,
    fixture,
    bindings,
    judge=None,
):
    """Observe into <run>/evidence, then evaluate it into <run>/evaluation."""
    evidence = Path(run) / "evidence"

    def recording(config, directory, **options):
        record = runner(config, directory, **options)
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if not (directory / "config.json").exists():
            write_record_json(directory / "config.json", config)
        if not (directory / "case.json").exists():
            write_record_json(directory / "case.json", record)
        return record

    try:
        issued = verifier.plan(scenario, Path(config_path), cases, mode, selected_case, fixture=fixture)
    except Exception:  # Mirrors the verifier CLI, where a failed plan runs nothing
        issued = None
    if issued is not None:
        observe(issued, Path(config_path), evidence, runner=recording, bindings=bindings)
    return verifier.evaluate(
        scenario, Path(config_path), evidence, cases, mode, selected_case, judge=judge, fixture=fixture
    )
