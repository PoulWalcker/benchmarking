"""The verifier's rubric seam: named checks, judge prose, and the reward it must not touch."""

import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock

from tests.test_expansion_scenarios import observation
from verification import rubric_facts
from verification import verify as verifier
from verification.contracts import Rejected
from verification.rubric import RecordedJudge, RunFacts, _judge_view
from verification.rubric_cards import CARDS, card_for
from verification.business import check_business_result
from verification.scenario_business import check_scenario_business_result
from verification.scenario_contracts import CONTRACTS, ROUTING_EDGES
from sapi_config_lab.coordinate.scenarios import all_cases
from tests.support.verifying import verify_with_runner

ROOT = Path(__file__).parents[1]
SCENARIO = "support-review-packet"
INPUTS = {
    "ticket": {"id": "S-31", "text": "Please check order A9137, delayed three days.", "days_overdue": 3},
    "invoices": [
        {"id": "B-1", "amount_minor": 1251, "currency": "AED"},
        {"id": "B-2", "amount_minor": 349, "currency": "AED"},
    ],
    "required_order_id": "A9137",
    "max_characters": 120,
}
DRAFT = "We are still checking order A9137 and will write again tomorrow."
ANSWERS = {"usefulness": "yes", "honesty": "yes", "actionability": "yes"}


def packet(text=DRAFT):
    """One honest support-review run, exactly as the obligations require it."""
    action = {"ticket_id": "S-31", "action": "escalate", "mode": "draft"}
    report = {"total_minor": 1600, "currency": "AED", "invoice_count": 2}
    reply = {"text": text}
    errors = [] if INPUTS["required_order_id"] in text else ["Include the order ID"]
    review = {"pass": not errors, "errors": errors}
    values = {
        "classify": {"category": "delivery", "priority": "high"},
        "escalate": action,
        "select": action,
        "validate": {"invoices": INPUTS["invoices"]},
        "total": {"amount_minor": 1600, "currency": "AED", "count": 2},
        "report": report,
        "draft": reply,
        "check": review,
    }
    return values, {"action": action, "invoice_report": report, "reply": reply, "review": review}


def observe(values, output):
    return observation(
        values,
        output,
        skipped=("normal",),
        dependencies=CONTRACTS[SCENARIO]["edges"],
    )


def facts_for(values, output, name="run"):
    return rubric_facts.observe(SCENARIO, INPUTS, observe(values, output), case={"name": name})


def reward(report):
    """What harbor/templates/test.sh writes: the verifier's exit status, nothing else."""
    return 1.0 if report["passed"] else 0.0


def stub_runner(config, artifacts, **options):
    """Compiles and executes nothing; the submission is rejected for want of provenance."""
    return {"status": "success", "output": {}, "mapping": {}}


class NamedCheckTests(unittest.TestCase):
    """Raised / did not raise, one obligation at a time."""

    def broken(self, name):
        values, output = packet()
        if name == "routing_single_action":
            normal = {"ticket_id": "S-31", "action": "normal_reply", "mode": "draft"}
            values["escalate"], values["select"], output["action"] = normal, normal, normal
        elif name == "ledger_report":
            wrong = {"total_minor": 1601, "currency": "AED", "invoice_count": 2}
            values["report"], output["invoice_report"] = wrong, wrong
        else:
            values, output = packet("We are checking your order.")
            values["check"] = output["review"] = {"pass": True, "errors": []}
        return values, output

    def test_an_honest_packet_holds_every_named_check(self):
        values, output = packet()
        self.assertEqual(
            facts_for(values, output)["checks"],
            {"routing_single_action": True, "ledger_report": True, "review_matches": True},
        )

    def test_each_named_check_fails_alone_and_acceptance_agrees(self):
        for name in ("routing_single_action", "ledger_report", "review_matches"):
            with self.subTest(check=name):
                values, output = self.broken(name)
                self.assertEqual(
                    facts_for(values, output)["checks"],
                    {
                        "routing_single_action": name != "routing_single_action",
                        "ledger_report": name != "ledger_report",
                        "review_matches": name != "review_matches",
                    },
                )
                # The same obligation, through the acceptance path that owns the reward.
                with self.assertRaises(Rejected):
                    check_scenario_business_result(SCENARIO, INPUTS, observe(values, output), "live")

    def test_a_malformed_run_reports_a_failed_check_and_never_raises(self):
        values, output = packet()
        values.pop("check")
        self.assertEqual(facts_for(values, output)["checks"]["review_matches"], False)

    def test_a_scenario_with_no_named_checks_produces_none(self):
        values, output = packet()
        self.assertEqual(rubric_facts.observe("revise-answer", INPUTS, observe(values, output))["checks"], {})


class JudgeProseTests(unittest.TestCase):
    def prose(self):
        values, output = packet()
        return facts_for(values, output)["prose"]

    def test_every_judged_source_the_card_declares_is_supplied(self):
        card = card_for(SCENARIO)
        declared = {
            source
            for criterion in card.criteria
            if criterion.evaluator == "llm"
            for source in criterion.required_evidence
        }
        self.assertTrue(declared)
        self.assertLessEqual(declared, set(self.prose()))

    def test_prose_is_the_run_s_own_draft_review_and_ticket(self):
        prose = self.prose()
        self.assertIn(DRAFT, prose["candidate"])
        self.assertIn("it passed", prose["candidate"])
        self.assertIn(INPUTS["ticket"]["text"], prose["environment"])
        self.assertIn("B-1 1251 AED", prose["environment"])

    def test_the_protected_narrative_is_supplied_and_never_forwarded(self):
        values, output = packet()
        run = facts_for(values, output)
        self.assertIn("routing_single_action held", run["prose"]["verification"])
        judge = RecordedJudge(ANSWERS)
        rubric_facts.evaluate(SCENARIO, [run], accepted=True, execution_pass=True, judge=judge)
        self.assertEqual(sorted(judge.requests[0].facts.prose), ["candidate", "environment"])
        self.assertEqual(
            sorted(_judge_view(card_for(SCENARIO), RunFacts(True, run["checks"], run["prose"])).prose),
            ["candidate", "environment"],
        )

    def test_cases_reach_the_judge_as_ordinals_not_fixture_names(self):
        runs = [facts_for(*packet(), name="wrong-order-id"), facts_for(*packet(), name="small-review-limit")]
        merged = rubric_facts._merged_prose(runs)
        self.assertIn("Run 1:", merged["candidate"])
        self.assertIn("Run 2:", merged["candidate"])
        for name in ("wrong-order-id", "small-review-limit"):
            self.assertNotIn(name, json.dumps(merged))


class EvaluationTests(unittest.TestCase):
    def evaluate(self, judge=None, *, accepted=True, execution_pass=True, runs=None):
        if runs is None:
            runs = [facts_for(*packet())]
        return rubric_facts.evaluate(SCENARIO, runs, accepted=accepted, execution_pass=execution_pass, judge=judge)

    def test_a_judge_scores_the_card_and_the_checks_carry_their_own_points(self):
        document = self.evaluate(RecordedJudge(ANSWERS))
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["score_0_10"], 10.0)
        self.assertEqual(document["deterministic_points"], 6.0)

    def test_a_judge_that_answers_no_leaves_the_six_deterministic_points(self):
        document = self.evaluate(RecordedJudge({name: "no" for name in ANSWERS}))
        self.assertEqual(document["score_0_10"], 6.0)
        self.assertEqual(document["normalized_reward"], 0.6)

    def test_without_a_judge_the_rubric_is_not_evaluated_rather_than_zero(self):
        document = self.evaluate(None)
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertIsNone(document["normalized_reward"])
        self.assertIn("no judge", document["reason"])
        # The observed checks are still reported; only the score is withheld.
        self.assertTrue(document["checks"]["routing_single_action"])

    def test_a_run_that_produced_no_check_is_not_scored_and_costs_no_judgement(self):
        judge = RecordedJudge(ANSWERS)
        document = self.evaluate(judge, runs=[])
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(judge.requests, [])

    def test_a_failed_check_scores_below_the_total_without_touching_acceptance(self):
        values, output = packet()
        wrong = {"total_minor": 1601, "currency": "AED", "invoice_count": 2}
        values["report"], output["invoice_report"] = wrong, wrong
        document = self.evaluate(RecordedJudge(ANSWERS), accepted=False, runs=[facts_for(values, output)])
        self.assertEqual(document["score_0_10"], 8.0)

    def test_a_check_must_hold_in_every_case(self):
        values, output = packet()
        wrong = {"total_minor": 1601, "currency": "AED", "invoice_count": 2}
        values["report"], output["invoice_report"] = wrong, wrong
        runs = [facts_for(*packet()), facts_for(values, output)]
        self.assertEqual(self.evaluate(RecordedJudge(ANSWERS), runs=runs)["score_0_10"], 8.0)

    def test_a_scenario_with_no_card_is_not_evaluated_at_all(self):
        # The two scenarios that are verified through a different path entirely.
        for scenario in ("revise-answer", "daily-digest"):
            with self.subTest(scenario=scenario):
                self.assertIsNone(rubric_facts.evaluate(scenario, [], accepted=True, execution_pass=True, judge=None))

    def test_a_binary_card_needs_no_judge_and_reads_acceptance(self):
        for accepted, total in ((True, 10.0), (False, 0.0)):
            with self.subTest(accepted=accepted):
                document = rubric_facts.evaluate(
                    "invoice-total", [], accepted=accepted, execution_pass=True, judge=None
                )
                self.assertEqual(document["status"], "complete")
                self.assertEqual(document["score_0_10"], total)

    def test_a_run_that_did_not_execute_is_unscored_and_costs_no_judgement(self):
        judge = RecordedJudge(ANSWERS)
        document = self.evaluate(judge, execution_pass=False)
        self.assertEqual(document["status"], "unscored")
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(judge.requests, [])


class VerifierSeamTests(unittest.TestCase):
    """verify_submission writes the evaluation beside the report and nothing else changes."""

    def verify(self, scenario, config, judge=None):
        directory = tempfile.mkdtemp()
        report = verify_with_runner(
            verifier,
            scenario,
            ROOT / config,
            Path(directory),
            selected_case=all_cases()[scenario]["positive"][0]["name"],
            runner=stub_runner,
            judge=judge,
            cases=all_cases()[scenario],
        )
        return Path(directory), report

    def test_the_evaluation_lands_beside_the_report(self):
        directory, report = self.verify(SCENARIO, "benchmarks/07-support-review-packet/config.yaml")
        self.assertFalse(report["passed"])
        document = json.loads((directory / "evaluation/evaluation.json").read_text())
        self.assertEqual(document["schema"], "sapi-lab-rubric-evaluation/v1")
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(document["execution_pass"], False)
        self.assertEqual(json.loads((directory / "evaluation/report.json").read_text()), report)

    def test_a_scenario_without_a_card_writes_no_evaluation(self):
        directory, _ = self.verify("revise-answer", "benchmarks/04-revise-answer/config.yaml")
        self.assertFalse((directory / "evaluation/evaluation.json").exists())

    def test_every_carded_scenario_writes_one_beside_the_report(self):
        for scenario, config in (
            ("ticket-routing", "benchmarks/02-ticket-routing/config.yaml"),
            ("competitor-report", "benchmarks/03-competitor-report/config.yaml"),
            ("bulletin-market-brief", "benchmarks/08-bulletin-market-brief/config.yaml"),
            ("priority-support-brief", "benchmarks/09-priority-support-brief/config.yaml"),
        ):
            with self.subTest(scenario=scenario):
                directory, report = self.verify(scenario, config)
                self.assertFalse(report["passed"])
                document = json.loads((directory / "evaluation/evaluation.json").read_text())
                self.assertEqual(document["rubric"]["id"], scenario)
                self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
                self.assertIsNone(document["score_0_10"])
                self.assertIn(reward(report), (0.0, 1.0))

    def test_the_rubric_cannot_change_the_verdict_or_the_reward(self):
        plain, without = self.verify(SCENARIO, "benchmarks/07-support-review-packet/config.yaml")
        judged, with_judge = self.verify(
            SCENARIO, "benchmarks/07-support-review-packet/config.yaml", RecordedJudge(ANSWERS)
        )
        self.assertEqual(
            json.dumps(without).replace(str(plain), "DIR"), json.dumps(with_judge).replace(str(judged), "DIR")
        )
        self.assertEqual(reward(without), reward(with_judge))
        self.assertIn(reward(without), (0.0, 1.0))
        self.assertNotIn("rubric", json.dumps(without))

    def test_an_unreachable_judge_is_recorded_and_never_fails_the_verifier(self):
        class Broken:
            mode, model = "broken", None

            def judge(self, request):
                raise RuntimeError("no transport here")

        values, output = packet()
        document = rubric_facts.evaluate(
            SCENARIO, [facts_for(values, output)], accepted=True, execution_pass=True, judge=Broken()
        )
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])

    def test_a_rubric_scored_scenario_still_rewards_exactly_one_or_zero(self):
        """The two numbers are separate: a quality score of 6.0 is never the reward."""
        judge = RecordedJudge({name: "no" for name in ANSWERS})
        for accepted, expected in ((True, 1.0), (False, 0.0)):
            with self.subTest(accepted=accepted):
                document = rubric_facts.evaluate(
                    SCENARIO, [facts_for(*packet())], accepted=accepted, execution_pass=True, judge=judge
                )
                self.assertEqual(document["score_0_10"], 6.0)
                self.assertEqual(document["normalized_reward"], 0.6)
                self.assertEqual(reward({"passed": accepted}), expected)
                self.assertNotEqual(document["normalized_reward"], expected)

    def test_the_viewer_export_leaves_a_rubric_document_alone(self):
        """It shares a file name with the task-evaluation document; the schema parts them."""
        from sapi_config_lab.evaluate.review_export import export_trial

        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "job" / "trial" / "verifier" / "evaluation"
            trial.mkdir(parents=True)
            document = rubric_facts.evaluate(
                SCENARIO, [facts_for(*packet())], accepted=True, execution_pass=True, judge=RecordedJudge(ANSWERS)
            )
            (trial / "evaluation.json").write_text(json.dumps(document))
            row = export_trial(trial / "evaluation.json", force=True, rewards=True, dry_run=False)
            self.assertEqual(row["written"], [])
            self.assertIn("Unexpected schema", row["skipped"][0]["reason"])
            self.assertFalse((trial / "reward.json").exists())

    def test_a_rubric_that_raises_still_leaves_a_report_and_a_verdict(self):
        with unittest.mock.patch.object(verifier, "score_rubric", side_effect=RuntimeError("broken rubric")):
            directory, report = self.verify(SCENARIO, "benchmarks/07-support-review-packet/config.yaml")
        self.assertEqual(json.loads((directory / "evaluation/report.json").read_text()), report)
        self.assertIn(reward(report), (0.0, 1.0))
        document = json.loads((directory / "evaluation/evaluation.json").read_text())
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIn("broken rubric", document["reason"])

    def test_the_container_derives_its_reward_from_acceptance_alone(self):
        script = (ROOT / "harbor/templates/test.sh").read_text()
        self.assertIn("printf '0\\n' > /logs/verifier/reward.txt", script)
        self.assertIn("printf '1\\n' > /logs/verifier/reward.txt", script)
        self.assertEqual(script.count("reward.txt"), 2)
        for forbidden in ("evaluation", "score", "rubric", "normalized"):
            self.assertNotIn(forbidden, script)

    def test_every_control_run_reads_the_reward_gate_from_one_place(self):
        from sapi_config_lab.coordinate.evaluation import control_passed

        def trial(reward, exception=None):
            row = {"task_name": "invoice-total", "rewards": {"reward": reward}, "exception": exception}
            return {**row, "acceptance": {"passed": True}}

        for agent, reward in (("oracle", 1.0), ("nop", 0.0)):
            self.assertTrue(control_passed(agent, trial(reward)))
            # A rubric score is a separate document; it must never read as a reward.
            for intruder in (0.732, 1.0 - reward, None, "1.0"):
                self.assertFalse(control_passed(agent, trial(intruder)))
            self.assertFalse(control_passed(agent, trial(reward, "boom")))


class StandaloneDistributionTests(unittest.TestCase):
    def test_the_rubric_seam_imports_flat_beside_its_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in (ROOT / "verification").glob("*.py"):
                (Path(directory) / source.name).write_text(source.read_text())
            probe = (
                "import sys;"
                "import rubric_facts, rubric_cards, rubric, business, scenario_business;"
                "assert rubric_facts.__package__ == '', 'imported as a package';"
                # The copied files reach no third party and no installed project.
                "assert not {'yaml', 'sapi_config_lab'} & set(sys.modules), sorted(sys.modules);"
                "assert rubric_facts.evaluate('revise-answer', [], accepted=True, execution_pass=True) is None;"
                # Every carded scenario resolves flat, including the two whose
                # obligations live in business.py rather than scenario_business.py.
                "assert set(rubric_cards.CARDS) == " + repr(set(CARDS)) + ";"
                "assert rubric_facts.evaluate('competitor-report', [], accepted=True,"
                " execution_pass=True)['status'] == 'not_evaluated';"
                "print(rubric_facts.evaluate('invoice-total', [], accepted=True, execution_pass=True)['score_0_10'])"
            )
            import subprocess
            import sys

            run = subprocess.run(
                [sys.executable, "-E", "-s", "-c", probe], cwd=directory, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), "10.0")


# --- The scenarios carded after support-review-packet ------------------------
#
# Each states the same four things 07 does: an honest run holds every named
# check, each check fails alone and acceptance agrees, the judge is handed the
# declared prose and nothing else, and a judge answering "no" leaves exactly the
# deterministic points standing.

CASES = all_cases()


def tagged(values, output, operations, **options):
    """A business.py observation: its events also name the operation each role ran."""
    run = observation(values, output, **options)
    for role, sid in run.roles.items():
        run.events[sid]["operation"] = operations[role]
    return run


ROUTING_CASE = CASES["ticket-routing"]["positive"][0]
ROUTING_OPERATIONS = {
    "classify": "ticket.classify",
    "escalate": "ticket.escalation_draft",
    "normal": "ticket.normal_draft",
    "select": "branch.select_one",
}


def routing_run(broken=None):
    """One high-priority routing run; `broken` fails exactly one named check."""
    action = {"ticket_id": ROUTING_CASE["inputs"]["ticket"]["id"], "action": "escalate", "mode": "draft"}
    values = {"classify": {"category": "delivery", "priority": "high"}, "escalate": action, "select": action}
    output = dict(action)
    skipped = ["normal"]
    if broken == "classification_matches_boundary":
        values["classify"] = {"category": "delivery", "priority": "normal"}
    elif broken == "single_branch_executed":
        skipped.append("classify")
    elif broken == "draft_is_the_selected_action":
        output = {**action, "action": "normal_reply"}
    elif broken == "skipped_branch_left_no_trace":
        values["normal"] = action
    return tagged(values, output, ROUTING_OPERATIONS, skipped=tuple(skipped), dependencies=ROUTING_EDGES)


REPORT_CASE = CASES["competitor-report"]["positive"][0]
REPORT_OPERATIONS = {
    "product": "research.product",
    "marketing": "research.marketing",
    "combine": "research.combine",
    "write": "research.write",
}
REPORT_EDGES = [("product", "combine"), ("marketing", "combine"), ("combine", "write")]
REPORT_TEXT = (
    "Invoicing, stock tracking and a public API are what small shops get, "
    "and webinars and partner referrals are how they hear about it."
)


def report_run(broken=None):
    """One grounded competitor report; `broken` fails exactly one named check."""
    product = {
        "summary": "Invoicing, stock tracking and a public API.",
        "evidence": "invoicing, stock tracking and a public API",
    }
    marketing = {
        "summary": "Small shops reached by webinars and partner referrals.",
        "evidence": "small shops using webinars and partner referrals",
    }
    actors = {"product": "product-sapi", "marketing": "marketing-sapi", "write": "writer-sapi"}
    report = REPORT_TEXT
    if broken == "analyses_independent":
        actors["marketing"] = "product-sapi"
    elif broken == "analyses_quote_their_source":
        product = {**product, "evidence": "a phrase the product material never used"}
    elif broken == "report_covers_both_sources":
        report = "Both source materials were read."
    combine = {"product": product, "marketing": marketing}
    if broken == "join_preserves_both_analyses":
        combine = {"product": {**product, "summary": "Something else entirely."}, "marketing": marketing}
    evidence = [product["evidence"], marketing["evidence"]]
    if broken == "report_carries_both_excerpts":
        evidence = evidence[:1]
    output = {"report": report, "evidence": evidence}
    values = {"product": product, "marketing": marketing, "combine": combine, "write": output}
    return tagged(values, output, REPORT_OPERATIONS, actors=actors, dependencies=REPORT_EDGES)


BULLETIN_CASE = CASES["bulletin-market-brief"]["positive"][0]


def bulletin_run(broken=None):
    """One bulletin digest and brief; `broken` fails exactly one named check."""
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
    actors = {"summarize": "a", "product": "b", "marketing": "c", "write": "d"}
    if broken == "digest_covers_every_article":
        digest = {**digest, "article_ids": ["BL-1", "BL-1"]}
    elif broken == "digest_grounded_in_articles":
        digest = {**digest, "text": digest["text"] + " SMS appointments are included."}
    elif broken == "four_distinct_actors":
        actors["marketing"] = "b"
    elif broken == "brief_merges_both_sources":
        brief = {**brief, "report": "Everything looks good."}
    preview = {"mode": "preview", **digest}
    if broken == "preview_repeats_the_digest":
        preview = {**preview, "mode": "final"}
    values = {
        "prepare": {"articles": BULLETIN_CASE["inputs"]["articles"]},
        "summarize": digest,
        "preview": preview,
        "product": product,
        "marketing": marketing,
        "combine": {"product": product, "marketing": marketing},
        "write": brief,
    }
    return observation(
        values,
        {"digest": preview, "brief": brief},
        actors=actors,
        dependencies=CONTRACTS["bulletin-market-brief"]["edges"],
    )


PRIORITY_CASE = CASES["priority-support-brief"]["positive"][0]


def priority_run(broken=None):
    """One escalated ticket and the brief it earned; `broken` fails one named check."""
    action = {"ticket_id": PRIORITY_CASE["inputs"]["ticket"]["id"], "action": "escalate", "mode": "draft"}
    product = {
        "summary": "Barcode scanning and offline inventory.",
        "evidence": "barcode scanning and offline inventory",
    }
    marketing = {
        "summary": "Local shops reached through partner referrals.",
        "evidence": "local shops through partner referrals",
    }
    brief = {
        "report": "Barcode scanning and offline inventory for local shops reached through partner referrals.",
        "evidence": [product["evidence"], marketing["evidence"]],
    }
    if broken == "routing_single_action":
        action = {**action, "action": "normal_reply"}
    elif broken == "research_matches_priority":
        brief = {**brief, "report": "Everything looks good."}
    values = {
        "classify": {"category": "delivery", "priority": "high"},
        "escalate": action,
        "select": action,
        "product": product,
        "marketing": marketing,
        "combine": {"product": product, "marketing": marketing},
        "write": brief,
    }
    return observation(
        values,
        {"action": action, "brief": brief},
        skipped=("normal",),
        dependencies=CONTRACTS["priority-support-brief"]["edges"],
    )


CARDED_SCENARIOS = {
    "ticket-routing": (ROUTING_CASE, routing_run),
    "competitor-report": (REPORT_CASE, report_run),
    "bulletin-market-brief": (BULLETIN_CASE, bulletin_run),
    "priority-support-brief": (PRIORITY_CASE, priority_run),
}


def judged_criteria(scenario):
    return [criterion for criterion in card_for(scenario).criteria if criterion.evaluator == "llm"]


class CardedScenarioTests(unittest.TestCase):
    """Every scenario carded beside 07, held to the promises 07 is held to."""

    def facts(self, scenario, broken=None):
        case, build = CARDED_SCENARIOS[scenario]
        return rubric_facts.observe(scenario, case["inputs"], build(broken), case=case)

    def test_the_card_names_exactly_the_obligations_the_run_measures(self):
        for scenario in CARDED_SCENARIOS:
            with self.subTest(scenario=scenario):
                declared = {
                    criterion.check_id
                    for criterion in card_for(scenario).criteria
                    if criterion.evaluator == "deterministic"
                }
                self.assertEqual(declared, set(self.facts(scenario)["checks"]))

    def test_an_honest_run_holds_every_named_check(self):
        for scenario in CARDED_SCENARIOS:
            with self.subTest(scenario=scenario):
                checks = self.facts(scenario)["checks"]
                self.assertTrue(checks)
                self.assertTrue(all(checks.values()), checks)

    def test_each_named_check_fails_alone_and_acceptance_agrees(self):
        for scenario, (case, build) in CARDED_SCENARIOS.items():
            for name in self.facts(scenario)["checks"]:
                with self.subTest(scenario=scenario, check=name):
                    self.assertEqual(
                        self.facts(scenario, name)["checks"],
                        {other: other != name for other in self.facts(scenario)["checks"]},
                    )
                    # The same obligation, through the acceptance path that owns the reward.
                    with self.assertRaises(Rejected):
                        check_business_result(scenario, case["inputs"], build(name), "live", case=case)

    def test_an_honest_run_is_accepted_unchanged(self):
        for scenario, (case, build) in CARDED_SCENARIOS.items():
            with self.subTest(scenario=scenario):
                result = check_business_result(scenario, case["inputs"], build(), "live", case=case)
                self.assertTrue(result["output_verified"])

    def test_the_judge_is_handed_the_declared_prose_and_nothing_else(self):
        for scenario in CARDED_SCENARIOS:
            with self.subTest(scenario=scenario):
                declared = sorted(
                    {source for criterion in judged_criteria(scenario) for source in criterion.required_evidence}
                )
                run = self.facts(scenario)
                if not declared:
                    # ticket-routing is deterministic throughout and supplies no prose.
                    self.assertEqual(run["prose"], {})
                    continue
                self.assertLessEqual(set(declared), set(run["prose"]))
                self.assertIn("verification", run["prose"])
                judge = RecordedJudge({criterion.id: "yes" for criterion in judged_criteria(scenario)})
                rubric_facts.evaluate(scenario, [run], accepted=True, execution_pass=True, judge=judge)
                self.assertEqual(sorted(judge.requests[0].facts.prose), declared)

    def test_the_judge_sees_no_check_name_no_verdict_and_no_fixture_name(self):
        for scenario, (case, build) in CARDED_SCENARIOS.items():
            if not judged_criteria(scenario):
                continue
            with self.subTest(scenario=scenario):
                runs = [self.facts(scenario), self.facts(scenario)]
                judge = RecordedJudge({criterion.id: "yes" for criterion in judged_criteria(scenario)})
                rubric_facts.evaluate(scenario, runs, accepted=True, execution_pass=True, judge=judge)
                view = json.dumps(dict(judge.requests[0].facts.prose), ensure_ascii=False)
                self.assertIn("Run 1:", view)
                self.assertIn("Run 2:", view)
                self.assertNotIn(case["name"], view)
                for name in runs[0]["checks"]:
                    self.assertNotIn(name, view)
                self.assertNotIn(runs[0]["prose"]["verification"], view)

    def test_a_judge_answering_no_leaves_exactly_the_deterministic_points(self):
        for scenario in CARDED_SCENARIOS:
            card = card_for(scenario)
            judged = judged_criteria(scenario)
            determined = float(sum(c.weight for c in card.criteria if c.evaluator == "deterministic"))
            with self.subTest(scenario=scenario):
                run = self.facts(scenario)
                for answer, expected in (("yes", 10.0), ("no", determined)):
                    judge = RecordedJudge({criterion.id: answer for criterion in judged}) if judged else None
                    document = rubric_facts.evaluate(scenario, [run], accepted=True, execution_pass=True, judge=judge)
                    self.assertEqual(document["status"], "complete")
                    self.assertEqual(document["score_0_10"], expected)
                    self.assertEqual(document["deterministic_points"], determined)

    def test_a_case_whose_facts_cannot_be_collected_never_raises_and_is_not_scored(self):
        """An unreadable fixture file is an environment fault, not a failed obligation."""
        with unittest.mock.patch.object(rubric_facts, "_checks", side_effect=OSError("no fixture file")):
            unmeasured = self.facts("competitor-report")
        self.assertEqual(unmeasured["checks"], {})
        self.assertEqual(unmeasured["prose"], {})
        judge = RecordedJudge({criterion.id: "yes" for criterion in judged_criteria("competitor-report")})
        for runs in ([unmeasured], [self.facts("competitor-report"), unmeasured]):
            with self.subTest(cases=len(runs)):
                document = rubric_facts.evaluate(
                    "competitor-report", runs, accepted=True, execution_pass=True, judge=judge
                )
                self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
                self.assertIsNone(document["score_0_10"])
                self.assertIsNone(document["normalized_reward"])

    def test_a_failed_check_costs_its_weight_and_nothing_else(self):
        for scenario in CARDED_SCENARIOS:
            card = card_for(scenario)
            judged = judged_criteria(scenario)
            for criterion in card.criteria:
                if criterion.evaluator != "deterministic":
                    continue
                with self.subTest(scenario=scenario, criterion=criterion.id):
                    judge = RecordedJudge({item.id: "yes" for item in judged}) if judged else None
                    document = rubric_facts.evaluate(
                        scenario,
                        [self.facts(scenario, criterion.check_id)],
                        accepted=False,
                        execution_pass=True,
                        judge=judge,
                    )
                    self.assertEqual(document["score_0_10"], 10.0 - criterion.weight)


if __name__ == "__main__":
    unittest.main()
