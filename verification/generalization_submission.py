"""Native acceptance runner for the two finite composition tasks."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from sapi_config_lab.runtime.execution import run_case
from sapi_config_lab.workflow import profile
from sapi_config_lab.experiments.provenance import source_manifest

if TYPE_CHECKING or __package__:
    from .contracts import Rejected, require
    from .generalization import analyze, verify_execution
    from .verify import UniqueLoader
else:
    from contracts import Rejected, require
    from generalization import analyze, verify_execution
    from verify import UniqueLoader


def verify_submission(scenario, config_path, report_dir, *, cases_path=None, runner=run_case):
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {"scenario": scenario, "passed": False, "cases": []}
    try:
        raw = config_path.read_bytes()
        (report_dir / "submission.yaml").write_bytes(raw)
        report["submission_sha256"] = hashlib.sha256(raw).hexdigest()
        config = yaml.load(raw, Loader=UniqueLoader)
        require(isinstance(config, dict) and config["workflow"]["id"] == scenario, "Wrong task identity")
        require(
            "lifecycle" not in config and "refinement" not in config["execution"],
            "Composition task uses the static profile",
        )
        require(config["activation"]["kind"] == "Callback", "Composition task requires Callback")
        source = cases_path or Path(__file__).with_name("cases.json")
        fixtures = source.read_bytes()
        report["fixture_sha256"] = hashlib.sha256(fixtures).hexdigest()
        cases = json.loads(fixtures)[scenario]
        report["runtime_source_manifest"] = {
            p: h
            for p, h in source_manifest(Path(profile.__file__).resolve().parents[3]).items()
            if p.startswith("src/")
        }
        base_output = None
        for case in cases:
            candidate = copy.deepcopy(config)
            candidate["workflow"]["inputs"] = case["inputs"]
            analyze(candidate, case)
            path = report_dir / "cases" / case["name"]
            record = runner(candidate, path)
            checked = verify_execution(candidate, case, record)
            report["cases"].append({"name": case["name"], "passed": True, "artifacts": str(path), **checked})
            if base_output is None:
                base_output = copy.deepcopy(record["output"])
        case = cases[0]
        for name in ("literal-output", "bypassed-source"):
            candidate = copy.deepcopy(config)
            candidate["workflow"]["inputs"] = case["inputs"]
            if name == "literal-output":
                candidate["workflow"]["output"] = base_output
            else:
                operation, field = (
                    ("invoices.sum", "invoices")
                    if scenario == "billing-bulletin-packet"
                    else ("research.marketing", "material")
                )
                step = next(step for step in candidate["workflow"]["steps"] if step["uses"] == operation)
                step["with"] = {
                    field: case["inputs"]["invoices"] if field == "invoices" else case["inputs"]["product_material"]
                }
            path = report_dir / "cases" / name
            record = runner(candidate, path)
            require(record["status"] == "success", "Negative control must succeed natively")
            try:
                verify_execution(candidate, case, record)
            except Rejected:
                report["cases"].append(
                    {"name": name, "passed": True, "business_rejected": True, "artifacts": str(path)}
                )
            else:
                raise Rejected("Verifier accepted an engine-successful broken composition")
        invalid = copy.deepcopy(config)
        invalid["workflow"]["inputs"] = copy.deepcopy(case["inputs"])
        field = "articles" if scenario == "billing-bulletin-packet" else "cooperatives_material"
        invalid["workflow"]["inputs"][field] = [] if field == "articles" else ""
        path = report_dir / "cases/empty-source"
        record = runner(invalid, path)
        require(
            record["status"] != "success" and record.get("output") is None, "Invalid input produced a successful packet"
        )
        report["cases"].append(
            {"name": "empty-source", "passed": True, "expected_rejection": True, "artifacts": str(path)}
        )
        if scenario == "billing-bulletin-packet":
            invalid = copy.deepcopy(config)
            invalid["workflow"]["inputs"] = copy.deepcopy(case["inputs"])
            invalid["workflow"]["inputs"]["invoices"][1]["currency"] = "USD"
            path = report_dir / "cases/mixed-currency-invoices"
            record = runner(invalid, path)
            require(
                record["status"] != "success" and record.get("output") is None, "Mixed invoice currencies were accepted"
            )
            report["cases"].append(
                {"name": "mixed-currency-invoices", "passed": True, "expected_rejection": True, "artifacts": str(path)}
            )
        report["passed"] = True
    except Exception as error:
        report.update(error=str(error), error_type=type(error).__name__)
    (report_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, choices=["billing-bulletin-packet", "two-audience-briefs"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    report = verify_submission(args.scenario, args.config, args.report_dir)
    print(json.dumps({"scenario": args.scenario, "passed": report["passed"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
