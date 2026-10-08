"""The verifier's rubric seam: named checks, judge prose, and the reward it must not touch."""

from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import unittest.mock

from sapi_config_lab.profile import read_bindings
from tests.support.invoice import CATALOG, cases
from tests.support.invoice import card as invoice_card
from tests.support.invoice import fixture as invoice_fixture
from tests.support.rubric import CARD
from tests.support.verifying import verify_with_runner
from verification import rubric_facts
from verification import verify as verifier
from verification.contracts import require
from verification.fixture import FixtureEvaluator
from verification.rubric import RecordedJudge, RunFacts, _judge_view

ROOT = Path(__file__).parents[1]
CARDS = {"invoice-total"}
SCENARIO = "rubric-sample"
INPUTS = {"source": "Sample source material."}
DRAFT = "A useful sample explanation."
ANSWERS = {"usefulness": "yes", "honesty": "yes", "actionability": "yes"}


def sample_run(text=DRAFT):
    return {"first": True, "second": True, "third": True}, {"text": text}


def observe(values, output):
    return SimpleNamespace(final={"output": output}, values=values)


def obligations(inputs, observation, *, case=None):
    def check(name):
        require(observation.values[name] is True, "Failed obligation: " + name)

    return {name + "_check": partial(check, name) for name in ("first", "second", "third")}


def business(inputs, observation, mode="stub", *, case=None):
    for check in obligations(inputs, observation, case=case).values():
        check()


def prose(inputs, observation, checks):
    return {
        "candidate": observation.final["output"]["text"],
        "environment": inputs["source"],
        "verification": ", ".join(name + (" held" if held else " failed") for name, held in checks.items()),
    }


FIXTURE = FixtureEvaluator(business=business, obligations=obligations, prose=prose, rubric=CARD)


def facts_for(values, output, name="run"):
    return rubric_facts.observe(SCENARIO, INPUTS, observe(values, output), case={"name": name}, evaluator=FIXTURE)


def evaluate_facts(runs, **options):
    return rubric_facts.evaluate(SCENARIO, runs, card=CARD, **options)


def reward(report):
    """What harbor/templates/test.sh writes: the verifier's exit status, nothing else."""
    return 1.0 if report["passed"] else 0.0


def stub_runner(config, artifacts, **options):
    """Compiles and executes nothing; the submission is rejected for want of provenance."""
    return {"status": "success", "output": {}, "mapping": {}}


class NamedCheckTests(unittest.TestCase):
    """Raised / did not raise, one obligation at a time."""

    def broken(self, name):
        values, output = sample_run()
        values[name.removesuffix("_check")] = False
        return values, output

    def test_an_honest_run_holds_every_named_check(self):
        values, output = sample_run()
        business(INPUTS, observe(values, output))
        declared = {criterion.check_id for criterion in CARD.criteria if criterion.evaluator == "deterministic"}
        self.assertEqual(declared, set(facts_for(values, output)["checks"]))
        self.assertEqual(
            facts_for(values, output)["checks"],
            {"first_check": True, "second_check": True, "third_check": True},
        )

    def test_each_named_check_fails_alone_and_acceptance_agrees(self):
        for name in ("first_check", "second_check", "third_check"):
            with self.subTest(check=name):
                values, output = self.broken(name)
                self.assertEqual(
                    facts_for(values, output)["checks"],
                    {
                        "first_check": name != "first_check",
                        "second_check": name != "second_check",
                        "third_check": name != "third_check",
                    },
                )
                # The same obligation, through the acceptance path that owns the reward.
                with self.assertRaises(AssertionError):
                    business(INPUTS, observe(values, output), "live")

    def test_a_malformed_run_reports_a_failed_check_and_never_raises(self):
        values, output = sample_run()
        values.pop("third")
        self.assertEqual(facts_for(values, output)["checks"]["third_check"], False)

    def test_a_scenario_with_no_named_checks_produces_none(self):
        values, output = sample_run()
        self.assertEqual(
            rubric_facts.observe("no-obligations", INPUTS, observe(values, output), evaluator=FixtureEvaluator())[
                "checks"
            ],
            {},
        )


class JudgeProseTests(unittest.TestCase):
    def prose(self):
        values, output = sample_run()
        return facts_for(values, output)["prose"]

    def test_every_judged_source_the_card_declares_is_supplied(self):
        card = CARD
        declared = {
            source
            for criterion in card.criteria
            if criterion.evaluator == "llm"
            for source in criterion.required_evidence
        }
        self.assertTrue(declared)
        self.assertLessEqual(declared, set(self.prose()))

    def test_prose_is_the_run_s_own_explanation_and_source(self):
        prose = self.prose()
        self.assertIn(DRAFT, prose["candidate"])
        self.assertIn(INPUTS["source"], prose["environment"])

    def test_the_protected_narrative_is_supplied_and_never_forwarded(self):
        values, output = sample_run()
        run = facts_for(values, output)
        self.assertIn("first_check held", run["prose"]["verification"])
        judge = RecordedJudge(ANSWERS)
        evaluate_facts([run], accepted=True, execution_pass=True, judge=judge)
        self.assertEqual(sorted(judge.requests[0].facts.prose), ["candidate", "environment"])
        self.assertEqual(
            sorted(_judge_view(CARD, RunFacts(True, run["checks"], run["prose"])).prose),
            ["candidate", "environment"],
        )

    def test_cases_reach_the_judge_as_ordinals_not_fixture_names(self):
        runs = [
            facts_for(*sample_run(), name="first-private-case"),
            facts_for(*sample_run(), name="second-private-case"),
        ]
        merged = rubric_facts._merged_prose(runs)
        self.assertIn("Run 1:", merged["candidate"])
        self.assertIn("Run 2:", merged["candidate"])
        for name in ("first-private-case", "second-private-case"):
            self.assertNotIn(name, json.dumps(merged))


class EvaluationTests(unittest.TestCase):
    def evaluate(self, judge=None, *, accepted=True, execution_pass=True, runs=None):
        if runs is None:
            runs = [facts_for(*sample_run())]
        return evaluate_facts(runs, accepted=accepted, execution_pass=execution_pass, judge=judge)

    def test_a_judge_scores_the_card_and_the_checks_carry_their_own_points(self):
        document = self.evaluate(RecordedJudge(ANSWERS))
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["score_0_10"], 10.0)
        self.assertEqual(document["deterministic_points"], 6.0)

    def test_a_judge_that_answers_no_leaves_the_six_deterministic_points(self):
        document = self.evaluate(RecordedJudge(dict.fromkeys(ANSWERS, "no")))
        self.assertEqual(document["score_0_10"], 6.0)
        self.assertEqual(document["normalized_reward"], 0.6)

    def test_without_a_judge_the_rubric_is_not_evaluated_rather_than_zero(self):
        document = self.evaluate(None)
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertIsNone(document["normalized_reward"])
        self.assertIn("no judge", document["reason"])
        # The observed checks are still reported; only the score is withheld.
        self.assertTrue(document["checks"]["first_check"])

    def test_a_run_that_produced_no_check_is_not_scored_and_costs_no_judgement(self):
        judge = RecordedJudge(ANSWERS)
        document = self.evaluate(judge, runs=[])
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(judge.requests, [])

    def test_a_failed_check_scores_below_the_total_without_touching_acceptance(self):
        values, output = sample_run()
        values["second"] = False
        document = self.evaluate(RecordedJudge(ANSWERS), accepted=False, runs=[facts_for(values, output)])
        self.assertEqual(document["score_0_10"], 8.0)

    def test_a_check_must_hold_in_every_case(self):
        values, output = sample_run()
        values["second"] = False
        runs = [facts_for(*sample_run()), facts_for(values, output)]
        self.assertEqual(self.evaluate(RecordedJudge(ANSWERS), runs=runs)["score_0_10"], 8.0)

    def test_a_scenario_with_no_card_is_not_evaluated_at_all(self):
        # Unknown names carry no implicit rubric.
        for scenario in ("no-card", "another-no-card"):
            with self.subTest(scenario=scenario):
                self.assertIsNone(
                    rubric_facts.evaluate(scenario, [], accepted=True, execution_pass=True, judge=None, card=None)
                )

    def test_a_binary_card_needs_no_judge_and_reads_acceptance(self):
        for accepted, total in ((True, 10.0), (False, 0.0)):
            with self.subTest(accepted=accepted):
                document = rubric_facts.evaluate(
                    "invoice-total", [], accepted=accepted, execution_pass=True, judge=None, card=invoice_card()
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

    def verify(self, judge=None, *, card=CARD):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        fixture = replace(invoice_fixture(), rubric=card)
        adapter = SimpleNamespace(
            plan=partial(verifier.plan, fixture=fixture), evaluate=partial(verifier.evaluate, fixture=fixture)
        )
        report = verify_with_runner(
            adapter,
            "invoice-total",
            ROOT / "benchmarks/01-invoice-total/config.yaml",
            directory,
            selected_case=cases()["positive"][0]["name"],
            runner=stub_runner,
            judge=judge,
            cases=cases(),
            fixture=fixture,
            bindings=read_bindings(CATALOG),
        )
        return directory, report

    def test_the_evaluation_lands_beside_the_report(self):
        directory, report = self.verify()
        self.assertFalse(report["passed"])
        document = json.loads((directory / "evaluation/evaluation.json").read_text())
        self.assertEqual(document["schema"], "sapi-lab-rubric-evaluation/v1")
        self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(document["execution_pass"], False)
        self.assertEqual(json.loads((directory / "evaluation/report.json").read_text()), report)

    def test_a_scenario_without_a_card_writes_no_evaluation(self):
        directory, _ = self.verify(card=None)
        self.assertFalse((directory / "evaluation/evaluation.json").exists())

    def test_the_rubric_cannot_change_the_verdict_or_the_reward(self):
        plain, without = self.verify()
        judged, with_judge = self.verify(RecordedJudge(ANSWERS))
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

        values, output = sample_run()
        document = evaluate_facts([facts_for(values, output)], accepted=True, execution_pass=True, judge=Broken())
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])

    def test_a_rubric_scored_scenario_still_rewards_exactly_one_or_zero(self):
        """The two numbers are separate: a quality score of 6.0 is never the reward."""
        judge = RecordedJudge(dict.fromkeys(ANSWERS, "no"))
        for accepted, expected in ((True, 1.0), (False, 0.0)):
            with self.subTest(accepted=accepted):
                document = evaluate_facts(
                    [facts_for(*sample_run())], accepted=accepted, execution_pass=True, judge=judge
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
            document = evaluate_facts(
                [facts_for(*sample_run())], accepted=True, execution_pass=True, judge=RecordedJudge(ANSWERS)
            )
            (trial / "evaluation.json").write_text(json.dumps(document))
            row = export_trial(trial / "evaluation.json", force=True, rewards=True, dry_run=False)
            self.assertEqual(row["written"], [])
            self.assertIn("Unexpected schema", row["skipped"][0]["reason"])
            self.assertFalse((trial / "reward.json").exists())

    def test_a_rubric_that_raises_still_leaves_a_report_and_a_verdict(self):
        with unittest.mock.patch.object(verifier, "score_rubric", side_effect=RuntimeError("broken rubric")):
            directory, report = self.verify()
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
        from sapi_config_lab.benchmark import load_benchmark
        from sapi_config_lab.coordinate.evaluation import control_passed

        benchmark = load_benchmark(ROOT / "benchmarks", ROOT / "benchmarks/01-invoice-total")

        def trial(agent, reward, exception=None):
            row = {"task_name": "invoice-total", "rewards": {"reward": reward}, "exception": exception}
            return {**row, "result": {"execution": agent == "oracle", "acceptance": agent == "oracle", "quality": None}}

        for agent, expected in (("oracle", 1.0), ("nop", 0.0)):
            self.assertTrue(control_passed(agent, trial(agent, expected), benchmark))
            # A rubric score is a separate document; it must never read as a reward.
            for intruder in (0.732, 1.0 - expected, None, "1.0"):
                self.assertFalse(control_passed(agent, trial(agent, intruder), benchmark))
            self.assertFalse(control_passed(agent, trial(agent, expected, "boom"), benchmark))


class StandaloneDistributionTests(unittest.TestCase):
    def test_the_rubric_seam_imports_flat_beside_its_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in (ROOT / "verification").glob("*.py"):
                (Path(directory) / source.name).write_text(source.read_text())
            probe = (
                "import sys;"
                "import rubric_facts, rubric;"
                "assert rubric_facts.__package__ == '', 'imported as a package';"
                "assert not {'yaml', 'sapi_config_lab'} & set(sys.modules), sorted(sys.modules);"
                "assert rubric_facts.evaluate('no-card', [], accepted=True, execution_pass=True, card=None) is None;"
                "card = rubric.RubricCard('explicit', '1', 'test', (rubric.Criterion(id='accepted', evaluator='deterministic', weight=10, question='Was it accepted?', check_id='accepted'),));"
                "print(rubric_facts.evaluate('explicit', [], accepted=True, execution_pass=True, card=card)['score_0_10'])"
            )
            import subprocess
            import sys

            run = subprocess.run(
                [sys.executable, "-E", "-s", "-c", probe], cwd=directory, capture_output=True, text=True, timeout=60
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), "10.0")


class FactFailureTests(unittest.TestCase):
    def test_an_observation_failure_produces_no_partial_facts(self):
        def broken(*args, **kwargs):
            raise OSError("unavailable fixture facts")

        values, output = sample_run()
        unmeasured = rubric_facts.observe(
            SCENARIO, INPUTS, observe(values, output), evaluator=replace(FIXTURE, obligations=broken)
        )
        self.assertEqual(unmeasured["checks"], {})
        self.assertEqual(unmeasured["prose"], {})
        judge = RecordedJudge(ANSWERS)
        for runs in ([unmeasured], [facts_for(*sample_run()), unmeasured]):
            with self.subTest(cases=len(runs)):
                document = evaluate_facts(runs, accepted=True, execution_pass=True, judge=judge)
                self.assertEqual(document["status"], rubric_facts.NOT_EVALUATED)
                self.assertIsNone(document["score_0_10"])
                self.assertIsNone(document["normalized_reward"])
        self.assertEqual(judge.requests, [])

    def test_each_failed_obligation_costs_only_its_weight(self):
        for criterion in CARD.criteria:
            if criterion.evaluator != "deterministic":
                continue
            values, output = sample_run()
            values[criterion.id] = False
            with self.subTest(criterion=criterion.id):
                document = evaluate_facts(
                    [facts_for(values, output)], accepted=False, execution_pass=True, judge=RecordedJudge(ANSWERS)
                )
                self.assertEqual(document["score_0_10"], 10.0 - criterion.weight)


if __name__ == "__main__":
    unittest.main()
