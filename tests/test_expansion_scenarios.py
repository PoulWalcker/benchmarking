"""Scenario business contracts and local JS probes; no native n8n claims."""

import copy
import unittest
from types import SimpleNamespace

from verification.contracts import Rejected
from verification.scenario_business import check_scenario_business_result


def observation(values, output, *, skipped=(), actors=None, dependencies=()):
    """Fabricate business observations, with only actual ancestors at each node."""
    roles = {role: "renamed_" + role for role in values.keys() | set(skipped)}
    events = {
        sid: {
            "step_id": sid,
            "status": "skipped" if role in skipped else "completed",
            "actor": (actors or {}).get(role),
        }
        for role, sid in roles.items()
    }

    def ancestors(role):
        direct = {source for source, target in dependencies if target == role}
        return direct | {earlier for source in direct for earlier in ancestors(source)}

    states = {}
    for role, sid in roles.items():
        visible = ancestors(role) | {role}
        states[sid] = {
            "steps": {roles[name]: copy.deepcopy(values[name]) for name in visible if name in values},
            "statuses": {roles[name]: events[roles[name]]["status"] for name in visible},
        }
    return SimpleNamespace(final={"output": output}, events=events, states=states, roles=roles)


def ledger_observation():
    inputs = {
        "domestic_invoices": [{"id": "D", "amount_minor": 1251, "currency": "AED"}],
        "export_invoices": [{"id": "X", "amount_minor": 73, "currency": "EUR"}],
    }
    domestic = {"total_minor": 1251, "currency": "AED", "invoice_count": 1}
    export = {"total_minor": 73, "currency": "EUR", "invoice_count": 1}
    values = {
        "domestic_validate": {"invoices": inputs["domestic_invoices"]},
        "domestic_sum": {"amount_minor": 1251, "currency": "AED", "count": 1},
        "domestic_report": domestic,
        "export_validate": {"invoices": inputs["export_invoices"]},
        "export_sum": {"amount_minor": 73, "currency": "EUR", "count": 1},
        "export_report": export,
    }
    obs = observation(
        values,
        {"domestic": domestic, "export": export},
        dependencies=[
            ("domestic_validate", "domestic_sum"),
            ("domestic_sum", "domestic_report"),
            ("export_validate", "export_sum"),
            ("export_sum", "export_report"),
        ],
    )
    return inputs, obs


class ExpansionBusinessTests(unittest.TestCase):
    def test_repeated_invoice_operations_keep_two_separate_ledgers(self):
        inputs, obs = ledger_observation()
        self.assertTrue(check_scenario_business_result("dual-ledger-closeout", inputs, obs)["output_verified"])
        obs.final["output"] = {"domestic": obs.final["output"]["export"], "export": obs.final["output"]["domestic"]}
        with self.assertRaisesRegex(Rejected, "ledger"):
            check_scenario_business_result("dual-ledger-closeout", inputs, obs)

    def test_support_packet_honestly_reports_utf16_review_and_error_order(self):
        inputs = {
            "ticket": {"id": "S", "text": "Order A9137", "days_overdue": 3},
            "invoices": [{"id": "B", "amount_minor": 1600, "currency": "AED"}],
            "required_order_id": "A9137",
            "max_characters": 1,
        }
        action = {"ticket_id": "S", "action": "escalate", "mode": "draft"}
        report = {"total_minor": 1600, "currency": "AED", "invoice_count": 1}
        reply = {"text": "😀"}
        review = {"pass": False, "errors": ["Include the order ID", "Shorten the reply"]}
        values = {
            "classify": {"category": "delivery", "priority": "high"},
            "escalate": action,
            "select": action,
            "validate": {"invoices": inputs["invoices"]},
            "total": {"amount_minor": 1600, "currency": "AED", "count": 1},
            "report": report,
            "draft": reply,
            "check": review,
        }
        from verification.scenario_contracts import CONTRACTS

        obs = observation(
            values,
            {"action": action, "invoice_report": report, "reply": reply, "review": review},
            skipped=("normal",),
            dependencies=CONTRACTS["support-review-packet"]["edges"],
        )
        self.assertTrue(check_scenario_business_result("support-review-packet", inputs, obs, "live")["output_verified"])
        for wrong in (
            {"pass": True, "errors": []},
            {"pass": False},
            {"pass": False, "errors": ["Shorten the reply", "Include the order ID"]},
        ):
            broken = copy.deepcopy(obs)
            broken.final["output"]["review"] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(Rejected):
                check_scenario_business_result("support-review-packet", inputs, broken, "live")

    def test_bulletin_brief_checks_derived_quotes_and_facts_separately(self):
        import json
        from pathlib import Path
        from verification.scenario_contracts import CONTRACTS

        inputs = json.loads(Path("verification/cases.json").read_text())["bulletin-market-brief"]["positive"][0][
            "inputs"
        ]
        digest = {"text": "Offline inventory and CSV export are supported.", "article_ids": ["BL-1", "BL-2"]}
        product = {"summary": "Offline inventory and CSV export.", "evidence": "Offline inventory and CSV export"}
        marketing = {
            "summary": "Rural cooperatives receive printed catalogues.",
            "evidence": "rural cooperatives through printed catalogues",
        }
        brief = {
            "report": "Offline inventory and CSV export for rural cooperatives through printed catalogues.",
            "evidence": [product["evidence"], marketing["evidence"]],
        }
        values = {
            "prepare": {"articles": inputs["articles"]},
            "summarize": digest,
            "preview": {"mode": "preview", **digest},
            "product": product,
            "marketing": marketing,
            "combine": {"product": product, "marketing": marketing},
            "write": brief,
        }
        obs = observation(
            values,
            {"digest": values["preview"], "brief": brief},
            actors={"summarize": "a", "product": "b", "marketing": "c", "write": "d"},
            dependencies=CONTRACTS["bulletin-market-brief"]["edges"],
        )
        self.assertTrue(check_scenario_business_result("bulletin-market-brief", inputs, obs, "live")["output_verified"])
        for role, field, wrong in [
            ("summarize", "article_ids", ["BL-1", "UNKNOWN"]),
            ("product", "evidence", inputs["articles"][0]["text"]),
            ("product", "summary", "An unrelated product."),
            ("write", "report", "Everything looks good."),
            ("write", "report", brief["report"] + " SMS appointments are included."),
        ]:
            broken = copy.deepcopy(obs)
            broken.states[broken.roles[role]]["steps"][broken.roles[role]][field] = wrong
            with self.subTest(role=role, field=field), self.assertRaises(Rejected):
                check_scenario_business_result("bulletin-market-brief", inputs, broken, "live")

    def test_normal_priority_propagates_all_skips_and_returns_null_brief(self):
        from verification.scenario_contracts import CONTRACTS

        inputs = {
            "ticket": {"id": "N", "text": "Two days late.", "days_overdue": 2},
            "product_material": "",
            "marketing_material": "",
        }
        action = {"ticket_id": "N", "action": "normal_reply", "mode": "draft"}
        values = {"classify": {"category": "delivery", "priority": "normal"}, "normal": action, "select": action}
        obs = observation(
            values,
            {"action": action, "brief": None},
            skipped=("escalate", "product", "marketing", "combine", "write"),
            dependencies=CONTRACTS["priority-support-brief"]["edges"],
        )
        self.assertTrue(check_scenario_business_result("priority-support-brief", inputs, obs)["output_verified"])
        for role in ("product", "marketing", "combine", "write"):
            wrong = copy.deepcopy(obs)
            wrong.states[wrong.roles["write"]]["statuses"].pop(wrong.roles[role])
            with self.subTest(role=role), self.assertRaises(Rejected):
                check_scenario_business_result("priority-support-brief", inputs, wrong)
        obs.final["output"]["brief"] = {"report": "Invented literal", "evidence": []}
        with self.assertRaises(Rejected):
            check_scenario_business_result("priority-support-brief", inputs, obs)


class ExpansionLocalGraphTests(unittest.TestCase):
    """Compile all fixtures through the narrow local JS driver, never real n8n."""

    @classmethod
    def setUpClass(cls):
        import json
        from sapi_config_lab.paths import CATALOG, workspace_root
        from sapi_config_lab.core.profile import read

        cls.root = workspace_root()
        cls.catalog = read(CATALOG)["operations"]
        cls.cases = json.loads((cls.root / "verification/cases.json").read_text())
        cls.configs = {
            read(path)["workflow"]["id"]: read(path) for path in sorted((cls.root / "configs").glob("0[6-9]-*.yaml"))
        }

    def execute(self, scenario, inputs, *, mutate=None):
        import json
        from pathlib import Path
        import subprocess
        import tempfile
        from sapi_config_lab.runtime.n8n.compiler import compile_demo

        artifact, mapping = compile_demo(self.configs[scenario], self.catalog)
        if mutate:
            mutate(artifact, mapping)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.json"
            path.write_text(json.dumps(artifact))
            data = Path(tmp) / "inputs.json"
            data.write_text(json.dumps(inputs))
            run = subprocess.run(
                ["node", str(self.root / "tests/support/run-export.mjs"), str(path), str(data)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        if run.returncode:
            raise RuntimeError(run.stderr)
        return json.loads(run.stdout)

    def test_all_positive_fixtures_satisfy_independent_business_obligations(self):
        from verification.roles import bind_roles
        from verification.scenario_contracts import CONTRACTS

        for scenario, config in self.configs.items():
            roles = bind_roles(scenario, config)
            for case in self.cases[scenario]["positive"]:
                with self.subTest(scenario=scenario, case=case["name"]):
                    result = self.execute(scenario, case["inputs"])
                    values = {role: result["steps"][sid] for role, sid in roles.items() if sid in result["steps"]}
                    skipped = [role for role, sid in roles.items() if result["statuses"][sid] == "skipped"]
                    actors = {
                        role: next(event for event in result["trace"] if event["step_id"] == sid).get("actor")
                        for role, sid in roles.items()
                    }
                    obs = observation(
                        values,
                        result["output"],
                        skipped=skipped,
                        actors=actors,
                        dependencies=CONTRACTS[scenario]["edges"],
                    )
                    self.assertTrue(
                        check_scenario_business_result(scenario, case["inputs"], obs, case=case)["output_verified"]
                    )

    def test_fresh_private_overlay_keeps_all_positive_and_negative_obligations(self):
        from sapi_config_lab.interfaces.expansion import fresh_case_overlay

        self.cases = {scenario: fresh_case_overlay(scenario)[scenario] for scenario in self.configs}
        self.test_all_positive_fixtures_satisfy_independent_business_obligations()
        self.test_invalid_fixtures_reject_in_local_driver()

    def test_invalid_fixtures_reject_in_local_driver(self):
        for scenario in self.configs:
            for case in self.cases[scenario]["negative"]:
                with (
                    self.subTest(scenario=scenario, case=case["name"]),
                    self.assertRaisesRegex(RuntimeError, __import__("re").escape(case["error"])),
                ):
                    self.execute(scenario, case["inputs"])

    def test_ledger_metamorphisms_do_not_mix_counts_or_currencies(self):
        scenario = "dual-ledger-closeout"
        results = {
            case["name"]: self.execute(scenario, case["inputs"])["output"] for case in self.cases[scenario]["positive"]
        }
        base = results["base-ledgers"]
        self.assertEqual(
            base,
            {
                "domestic": {"total_minor": 1251, "currency": "AED", "invoice_count": 2},
                "export": {"total_minor": 283, "currency": "EUR", "invoice_count": 2},
            },
        )
        self.assertEqual(results["reversed-ledgers"], base)
        self.assertEqual(results["domestic-zero-added"], {**base, "domestic": {**base["domestic"], "invoice_count": 3}})
        self.assertEqual(results["export-amount-changed"], {**base, "export": {**base["export"], "total_minor": 284}})

    def test_support_boundary_and_same_reply_review_metamorphisms(self):
        scenario = "support-review-packet"
        results = {
            case["name"]: self.execute(scenario, case["inputs"])["output"] for case in self.cases[scenario]["positive"]
        }
        self.assertEqual(results["high-packet"]["review"], {"pass": False, "errors": ["Include the order ID"]})
        self.assertEqual(results["small-review-limit"]["reply"], results["high-packet"]["reply"])
        self.assertEqual(
            results["small-review-limit"]["review"],
            {"pass": False, "errors": ["Include the order ID", "Shorten the reply"]},
        )
        self.assertEqual(results["invoice-amount-changed"]["action"], results["high-packet"]["action"])
        self.assertEqual(results["invoice-amount-changed"]["invoice_report"]["total_minor"], 1601)
        self.assertEqual(results["normal-packet"]["action"]["action"], "normal_reply")
        self.assertEqual(results["boundary-high"]["action"]["action"], "escalate")

    def test_all_four_optional_region_guards_are_required(self):
        from verification.roles import bind_roles

        config = self.configs["priority-support-brief"]
        for role in ("product", "marketing", "combine", "write"):
            broken = copy.deepcopy(config)
            next(step for step in broken["workflow"]["steps"] if step["id"] == role).pop("when")
            with self.subTest(role=role), self.assertRaises(Rejected):
                bind_roles("priority-support-brief", broken)

    def test_full_classification_ticket_cannot_enter_reply_closed_schema(self):
        from sapi_config_lab.runtime.n8n.compiler import compile_n8n

        config = copy.deepcopy(self.configs["support-review-packet"])
        next(step for step in config["workflow"]["steps"] if step["uses"] == "reply.generate")["with"]["ticket"] = {
            "ref": "inputs.ticket"
        }
        artifact, _ = compile_n8n(config, self.catalog, llm_mode="live", bridge_url="http://not-called.invalid")
        prepare = next(node for node in artifact["nodes"] if node["name"] == "Prepare draft")
        import json
        import subprocess

        envelope = {"inputs": config["workflow"]["inputs"], "steps": {}, "statuses": {}, "events": {}}
        code = 'const $workflow = {id: "local"}, $execution = {id: "schema-probe"};\n'
        code += "const $input = {all: () => [{json: " + json.dumps(envelope) + "}]};\n"
        # Only execute Prepare: schema rejection must precede any HTTP dispatch.
        code += prepare["parameters"]["jsCode"]
        run = subprocess.run(["node", "-e", "new Function(" + json.dumps(code) + ")()"], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Schema violation at inputs.ticket", run.stderr)
