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
from verification.rubric_cards import card_for
from verification.scenario_business import check_scenario_business_result
from verification.scenario_contracts import CONTRACTS

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
        self.assertEqual(rubric_facts.observe("ticket-routing", INPUTS, observe(values, output))["checks"], {})


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
        for scenario in ("ticket-routing", "competitor-report", "bulletin-market-brief", "revise-answer"):
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
        report = verifier.verify_submission(
            scenario,
            ROOT / config,
            Path(directory),
            selected_case=json.loads((ROOT / "verification/cases.json").read_text())[scenario]["positive"][0]["name"],
            runner=stub_runner,
            judge=judge,
        )
        return Path(directory), report

    def test_the_evaluation_lands_beside_the_report(self):
        directory, report = self.verify(SCENARIO, "configs/07-support-review-packet.yaml")
        self.assertFalse(report["passed"])
        document = json.loads((directory / "evaluation.json").read_text())
        self.assertEqual(document["schema"], "sapi-lab-rubric-evaluation/v1")
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(document["execution_pass"], False)
        self.assertEqual(json.loads((directory / "report.json").read_text()), report)

    def test_a_scenario_without_a_card_writes_no_evaluation(self):
        directory, _ = self.verify("ticket-routing", "configs/02-ticket-routing.yaml")
        self.assertFalse((directory / "evaluation.json").exists())

    def test_the_rubric_cannot_change_the_verdict_or_the_reward(self):
        plain, without = self.verify(SCENARIO, "configs/07-support-review-packet.yaml")
        judged, with_judge = self.verify(SCENARIO, "configs/07-support-review-packet.yaml", RecordedJudge(ANSWERS))
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
        from sapi_config_lab.interfaces.review_export import export_trial

        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory) / "job" / "trial" / "verifier"
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
        with unittest.mock.patch.object(verifier, "evaluate", side_effect=RuntimeError("broken rubric")):
            directory, report = self.verify(SCENARIO, "configs/07-support-review-packet.yaml")
        self.assertEqual(json.loads((directory / "report.json").read_text()), report)
        self.assertIn(reward(report), (0.0, 1.0))
        document = json.loads((directory / "evaluation.json").read_text())
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIn("broken rubric", document["reason"])

    def test_the_container_derives_its_reward_from_acceptance_alone(self):
        script = (ROOT / "harbor/templates/test.sh").read_text()
        self.assertIn("printf '0\\n' > /logs/verifier/reward.txt", script)
        self.assertIn("printf '1\\n' > /logs/verifier/reward.txt", script)
        self.assertEqual(script.count("reward.txt"), 2)
        for forbidden in ("evaluation", "score", "rubric", "normalized"):
            self.assertNotIn(forbidden, script)


class StandaloneDistributionTests(unittest.TestCase):
    def test_the_rubric_seam_imports_flat_beside_its_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in (ROOT / "verification").glob("*.py"):
                (Path(directory) / source.name).write_text(source.read_text())
            probe = (
                "import sys;"
                "import rubric_facts, rubric_cards, rubric, scenario_business;"
                "assert rubric_facts.__package__ == '', 'imported as a package';"
                # The copied files reach no third party and no installed project.
                "assert not {'yaml', 'sapi_config_lab'} & set(sys.modules), sorted(sys.modules);"
                "assert rubric_facts.evaluate('ticket-routing', [], accepted=True, execution_pass=True) is None;"
                "print(rubric_facts.evaluate('invoice-total', [], accepted=True, execution_pass=True)['score_0_10'])"
            )
            import subprocess
            import sys

            run = subprocess.run(
                [sys.executable, "-E", "-s", "-c", probe], cwd=directory, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), "10.0")


if __name__ == "__main__":
    unittest.main()
