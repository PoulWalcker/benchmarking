"""Plan, observe and evaluate in one call, with an injected runner.

The container runs these as three processes (harbor/templates/test.sh). Tests
compose them here so a fake runner can stand in for n8n; the verifier itself
never receives the runner.
"""

from pathlib import Path

from sapi_config_lab.coordinate.observe import observe
from sapi_config_lab.evidence import write_record_json


def verify_with_runner(
    verifier, scenario, config_path, evidence, mode="stub", selected_case=None, *, runner, cases, judge=None
):
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
        issued = verifier.plan(scenario, Path(config_path), cases, mode, selected_case)
    except Exception:
        issued = None
    if issued is not None:
        observe(issued, Path(config_path), Path(evidence), runner=recording)
    return verifier.evaluate(scenario, Path(config_path), Path(evidence), cases, mode, selected_case, judge=judge)
