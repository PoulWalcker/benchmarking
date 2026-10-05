"""Rubric scoring tests use fabricated run facts and never reach a model."""

import asyncio
from decimal import Decimal
import unittest

from sapi_config_lab.experiments import task_evaluation
from verification import rubric
from verification.rubric import (
    Criterion,
    JudgeReply,
    RecordedJudge,
    RubricCard,
    RubricError,
    RunFacts,
    score,
)
from verification.rubric_cards import CARDS, SUPPORT_REVIEW_PACKET, card_for


# The published criteria of reports/checkout-live-20261005, as scored upstream.
# Weights, answers and the resulting total are upstream's; the evidence sources
# follow this module's rule that no llm criterion reads protected verification.
CHECKOUT = RubricCard(
    id="production-checkout-recovery",
    version="1.0.0",
    origin="upstream",
    criteria=(
        Criterion(
            "outcome", "Do varied EUR checkout cases produce the correct charges?", 3, "deterministic", "eur_fixed"
        ),
        Criterion(
            "regression", "Are USD checkout and invalid-payment rejection preserved?", 1, "deterministic", "regressions"
        ),
        Criterion(
            "scope",
            "Was a valid minimal checkout patch applied without changing other state?",
            1,
            "deterministic",
            "safe_patch",
        ),
        Criterion(
            "verification",
            "Did the solution observe a failing test before repair and passing tests afterward?",
            1,
            "deterministic",
            "tested_recovery",
        ),
        Criterion(
            "evidence",
            "Is the decision grounded in the provided facts and observed results?",
            2,
            "llm",
            anchors={"yes": "Supported.", "maybe": "Partly supported.", "no": "Unsupported."},
            required_evidence=("environment", "candidate"),
        ),
        Criterion(
            "communication",
            "Is the final explanation clear, useful, and actionable?",
            1,
            "llm",
            anchors={"yes": "Clear.", "maybe": "Vague in one place.", "no": "Unusable."},
            required_evidence=("candidate",),
        ),
        Criterion(
            "honesty",
            "Do completion claims accurately distinguish observed success, proposals, and unresolved work?",
            1,
            "llm",
            anchors={"yes": "Accurate.", "maybe": "One claim overstated.", "no": "Contradicted."},
            required_evidence=("environment", "candidate"),
        ),
    ),
)
CHECKOUT_FACTS = RunFacts(
    execution_pass=True,
    checks={"eur_fixed": True, "regressions": True, "safe_patch": True, "tested_recovery": True},
    prose={"environment": "recorded session", "candidate": "final answer", "verification": "protected checks"},
    refs={"communication": ("candidate-final",)},
    run_digest="checkout-820f253abbb840dbbedd13cb4bdf33c8",
)
CHECKOUT_ANSWERS = {"evidence": "yes", "communication": "maybe", "honesty": "yes"}

# One llm criterion answered "maybe" scores 3.30, whose float division by ten
# loses a digit. It is the smallest card that exercises the reward arithmetic.
LOSSY = RubricCard(
    id="lossy",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            "single",
            "Is the draft usable?",
            1,
            "llm",
            anchors={"yes": "a", "maybe": "b", "no": "c"},
            required_evidence=("candidate",),
        ),
    ),
)


def support_facts(**overrides):
    facts = {
        "execution_pass": True,
        "checks": {"routing_single_action": True, "ledger_report": True, "review_matches": True},
        "prose": {"candidate": "draft and packet", "environment": "ticket and invoices", "verification": "review"},
        "refs": {"usefulness": ("candidate-final",)},
        "run_digest": "support-0001",
    }
    facts.update(overrides)
    return RunFacts(**facts)


def support_judge(**overrides):
    answers = {"usefulness": "yes", "honesty": "yes", "actionability": "yes"}
    answers.update(overrides)
    return RecordedJudge(answers)


class SurfaceTests(unittest.TestCase):
    def test_the_module_exposes_only_the_documented_scoring_surface(self):
        # Guards the constraint itself. A helper that turns a score into a reward,
        # or any other new public name, has to be argued for here first.
        imported = {
            "annotations",
            "asyncio",
            "hashlib",
            "json",
            "dataclass",
            "field",
            "Mapping",
            "MappingProxyType",
            "Decimal",
            "ROUND_HALF_UP",
            "Any",
            "Literal",
            "Protocol",
        }
        public = {name for name in vars(rubric) if not name.startswith("_")} - imported
        self.assertEqual(
            public,
            {
                "Answer",
                "Origin",
                "Document",
                "SCHEMA",
                "PROMPT_VERSION",
                "ANSWER_VALUES",
                "digest",
                "RubricError",
                "Criterion",
                "RubricCard",
                "RunFacts",
                "JudgeView",
                "JudgeRequest",
                "JudgeReply",
                "Judge",
                "RecordedJudge",
                "score",
            },
        )

    def test_the_document_claims_its_own_schema_not_the_task_evaluation_one(self):
        document = score(SUPPORT_REVIEW_PACKET, support_facts(), support_judge())
        self.assertEqual(document["schema"], "sapi-lab-rubric-evaluation/v1")
        self.assertNotEqual(document["schema"], task_evaluation.SCHEMA)

    def test_the_canonical_digest_agrees_with_the_upstream_adapter(self):
        for sample in ({"b": 1, "a": [2, None, True]}, {"text": "café — résumé"}, [], 0.33):
            with self.subTest(sample=sample):
                self.assertEqual(rubric.digest(sample), task_evaluation.digest(sample))


class RewardInvariantTests(unittest.TestCase):
    def test_no_scored_document_carries_a_harbor_reward_payload(self):
        # Every oracle/nop control gate asserts {"reward": 1.0} or {"reward": 0.0}
        # exactly, so nothing this module emits may be mistaken for that payload.
        documents = [
            score(SUPPORT_REVIEW_PACKET, support_facts(), support_judge(actionability="maybe")),
            score(SUPPORT_REVIEW_PACKET, support_facts(), RecordedJudge({})),
            score(SUPPORT_REVIEW_PACKET, support_facts(execution_pass=False), support_judge()),
            score(card_for("invoice-total"), RunFacts(True, {"accepted": True})),
            score(card_for("invoice-total"), RunFacts(True, {"accepted": False})),
        ]
        for document in documents:
            with self.subTest(status=document["status"]):
                self.assertNotIn("rewards", _every_key(document))
                self.assertNotIn("reward", _every_key(document))
                reward = document["normalized_reward"]
                self.assertTrue(reward is None or 0.0 <= reward <= 1.0)
                self.assertTrue(document["score_0_10"] is None or 0.0 <= document["score_0_10"] <= 10.0)

    def test_binary_cards_keep_the_existing_pass_fail_behaviour(self):
        for scenario in ("invoice-total", "dual-ledger-closeout"):
            card = card_for(scenario)
            self.assertFalse(card.needs_judge)
            accepted = score(card, RunFacts(execution_pass=True, checks={"accepted": True}))
            rejected = score(card, RunFacts(execution_pass=True, checks={"accepted": False}))
            with self.subTest(scenario=scenario):
                self.assertEqual((accepted["score_0_10"], accepted["normalized_reward"]), (10.0, 1.0))
                self.assertEqual((rejected["score_0_10"], rejected["normalized_reward"]), (0.0, 0.0))


def _every_key(value):
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _every_key(item)}
    if isinstance(value, list):
        return {key for item in value for key in _every_key(item)}
    return set()


class DispatchTests(unittest.TestCase):
    def test_a_deterministic_card_never_touches_a_judge_that_was_passed(self):
        judge = RecordedJudge({"usefulness": "yes"})
        document = score(card_for("invoice-total"), RunFacts(True, {"accepted": True}), judge)
        self.assertEqual(judge.requests, [])
        self.assertIsNone(document["judge"])
        self.assertIsNone(document["judge_error"])
        self.assertEqual(document["status"], "complete")

    def test_one_run_costs_at_most_one_dispatch(self):
        judge = support_judge()
        score(SUPPORT_REVIEW_PACKET, support_facts(), judge)
        self.assertEqual(len(judge.requests), 1)

    def test_a_non_executing_run_is_never_charged_for_a_judgement_it_discards(self):
        judge = support_judge()
        document = score(SUPPORT_REVIEW_PACKET, support_facts(execution_pass=False), judge)
        self.assertEqual(judge.requests, [])
        self.assertEqual(document["status"], "unscored")
        self.assertIsNone(document["judge_error"])
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertIsNone(rows["usefulness"]["answer"])
        self.assertEqual(rows["usefulness"]["reason"], "No judge was asked: the run did not execute")

    def test_a_judge_fault_outranks_an_unscored_status(self):
        # An operator filtering `status` for outages must still see every one.
        self.assertEqual(
            score(SUPPORT_REVIEW_PACKET, support_facts(), RecordedJudge({}))["status"],
            "judge_failed",
        )


class JudgeVisibilityTests(unittest.TestCase):
    def test_the_judge_receives_no_execution_flag_and_no_checks_at_all(self):
        judge = support_judge()
        score(SUPPORT_REVIEW_PACKET, support_facts(), judge)
        facts = judge.requests[0].facts
        # Withheld means absent. A False flag would assert the run did not execute.
        self.assertFalse(hasattr(facts, "execution_pass"))
        self.assertFalse(hasattr(facts, "checks"))

    def test_the_judge_never_sees_the_protected_verification_narrative(self):
        judge = support_judge()
        score(SUPPORT_REVIEW_PACKET, support_facts(), judge)
        facts = judge.requests[0].facts
        self.assertEqual(set(facts.prose), {"candidate", "environment"})
        self.assertNotIn("verification", facts.prose)

    def test_a_card_whose_llm_criterion_demands_verification_is_refused(self):
        card = RubricCard(
            "t",
            "1.0.0",
            "local",
            (
                Criterion(
                    "q",
                    "?",
                    1,
                    "llm",
                    anchors={"yes": "a", "maybe": "b", "no": "c"},
                    required_evidence=("verification",),
                ),
            ),
        )
        with self.assertRaisesRegex(RubricError, "must not read protected verification"):
            score(card, RunFacts(True, {}, prose={"verification": "x"}), support_judge())

    def test_the_judge_receives_only_the_criteria_it_must_answer(self):
        judge = support_judge()
        score(SUPPORT_REVIEW_PACKET, support_facts(), judge)
        request = judge.requests[0]
        self.assertEqual(
            tuple(criterion.id for criterion in request.criteria), ("usefulness", "honesty", "actionability")
        )
        # No deterministic check name or question crosses the seam.
        self.assertEqual([criterion.check_id for criterion in request.criteria], ["", "", ""])
        self.assertEqual(request.card_digest, SUPPORT_REVIEW_PACKET.digest())

    def test_judged_rows_do_not_claim_to_hold_judge_citations(self):
        document = score(SUPPORT_REVIEW_PACKET, support_facts(), support_judge())
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertNotIn("evidence_refs", rows["usefulness"])
        self.assertEqual(rows["usefulness"]["refs_supplied"], ["candidate-final"])
        self.assertEqual(rows["routing"]["refs_supplied"], ["check-routing_single_action"])


class ImmutabilityTests(unittest.TestCase):
    def test_a_judge_cannot_change_the_card_it_is_handed(self):
        refused = []

        class MutatingJudge(RecordedJudge):
            def judge(self, request):
                for criterion in request.criteria:
                    try:
                        criterion.anchors["maybe"] = "rewritten"
                    except TypeError as error:
                        refused.append(error)
                return super().judge(request)

        before = SUPPORT_REVIEW_PACKET.digest()
        document = score(SUPPORT_REVIEW_PACKET, support_facts(), MutatingJudge({"usefulness": "yes"}))
        self.assertEqual(len(refused), 3)  # the write is refused, not merely undone
        self.assertEqual(SUPPORT_REVIEW_PACKET.digest(), before)
        self.assertEqual(document["rubric"]["digest"], before)
        self.assertEqual(
            SUPPORT_REVIEW_PACKET.criteria[3].anchors["maybe"],
            "Relevant but generic; an agent must add something substantial.",
        )

    def test_mutating_a_dict_after_construction_does_not_change_a_card(self):
        anchors = {"yes": "a", "maybe": "b", "no": "c"}
        values = {"yes": 1.0, "maybe": 0.33, "no": 0.0}
        card = RubricCard(
            "t",
            "1.0.0",
            "local",
            (Criterion("q", "?", 1, "llm", anchors=anchors, required_evidence=("candidate",)),),
            answer_values=values,
        )
        before = card.digest()
        anchors["maybe"] = "rewritten"
        values["maybe"] = 1.0
        self.assertEqual(card.digest(), before)
        self.assertEqual(card.answer_values["maybe"], 0.33)
        self.assertEqual(card.criteria[0].anchors["maybe"], "b")

    def test_mutating_a_dict_after_construction_does_not_change_the_run_facts(self):
        checks = {"accepted": True}
        facts = RunFacts(execution_pass=True, checks=checks)
        checks["accepted"] = False
        self.assertEqual(score(card_for("invoice-total"), facts)["score_0_10"], 10.0)


class AnswerValueTests(unittest.TestCase):
    def test_a_card_that_reweights_an_answer_is_refused(self):
        for values in (
            {"yes": 7.5, "maybe": 0.33, "no": -3.0},
            {"yes": 1.0, "maybe": 0.5, "no": 0.0},
            {"yes": 1.0, "maybe": 0.33, "no": -0.0001},
            {"yes": float("nan"), "maybe": 0.33, "no": 0.0},
            {"yes": float("inf"), "maybe": 0.33, "no": 0.0},
            {"yes": True, "maybe": 0.33, "no": 0.0},
            {"yes": "1", "maybe": 0.33, "no": 0.0},
        ):
            card = RubricCard("t", "1.0.0", "local", card_for("invoice-total").criteria, answer_values=values)
            with self.subTest(values=values), self.assertRaisesRegex(RubricError, "canonical answer values"):
                score(card, RunFacts(True, {"accepted": True}))

    def test_a_card_stating_the_canonical_values_as_integers_is_accepted(self):
        card = RubricCard(
            "t", "1.0.0", "local", card_for("invoice-total").criteria, answer_values={"yes": 1, "maybe": 0.33, "no": 0}
        )
        self.assertEqual(score(card, RunFacts(True, {"accepted": True}))["score_0_10"], 10.0)

    def test_a_non_numeric_answer_table_is_refused_before_any_dispatch(self):
        # It used to pass validation, spend the dispatch and then raise from digest().
        judge = support_judge()
        card = RubricCard(
            "t",
            "1.0.0",
            "local",
            SUPPORT_REVIEW_PACKET.criteria,
            answer_values={"yes": float("nan"), "maybe": 0.33, "no": 0.0},
        )
        with self.assertRaises(RubricError):
            score(card, support_facts(), judge)
        self.assertEqual(judge.requests, [])


class ValidationTests(unittest.TestCase):
    def test_a_missing_deterministic_check_is_an_error_not_a_no(self):
        with self.assertRaisesRegex(RubricError, "Missing deterministic check: ledger_report"):
            score(SUPPORT_REVIEW_PACKET, support_facts(checks={"routing_single_action": True, "review_matches": True}))
        with self.assertRaisesRegex(RubricError, "Non-boolean deterministic check"):
            score(card_for("invoice-total"), RunFacts(True, {"accepted": "yes"}))

    def test_unusable_cards_and_facts_are_rejected_before_scoring(self):
        anchors = {"yes": "a", "maybe": "b", "no": "c"}
        good = Criterion("a", "?", 1, "deterministic", "check_a")
        cases = {
            "Duplicate": (good, Criterion("a", "?", 1, "deterministic", "check_a")),
            "positive integer": (good, Criterion("b", "?", 0, "deterministic", "check_b")),
            "must not name a deterministic check": (good, Criterion("b", "?", 1, "llm", "check_b", anchors)),
            "needs a yes, maybe and no anchor": (good, Criterion("b", "?", 1, "llm", anchors={"yes": "a"})),
            "Unsupported evidence source": (
                good,
                Criterion("b", "?", 1, "deterministic", "check_b", required_evidence=("rumour",)),
            ),
            "names no evidence source": (
                good,
                Criterion("b", "?", 1, "deterministic", "check_b", required_evidence=("candidate",)),
            ),
            "Unsupported evaluator": (good, Criterion("b", "?", 1, "oracle", "check_b")),
        }
        for message, criteria in cases.items():
            card = RubricCard("t", "1.0.0", "local", criteria)
            with self.subTest(message=message), self.assertRaisesRegex(RubricError, message):
                score(card, RunFacts(True, {"check_a": True, "check_b": True}), support_judge())
        with self.assertRaisesRegex(RubricError, "sum above zero"):
            score(RubricCard("t", "1.0.0", "local", ()), RunFacts(True, {}))
        with self.assertRaisesRegex(RubricError, "execution_pass must be a boolean"):
            score(card_for("invoice-total"), RunFacts(1, {"accepted": True}))

    def test_an_llm_card_requires_a_judge(self):
        with self.assertRaisesRegex(RubricError, "pass a judge"):
            score(SUPPORT_REVIEW_PACKET, support_facts())

    def test_required_evidence_must_be_satisfiable_before_a_judge_is_asked(self):
        judge = support_judge()
        with self.assertRaisesRegex(RubricError, "honesty lacks: environment"):
            score(SUPPORT_REVIEW_PACKET, support_facts(prose={"candidate": "d"}), judge)
        self.assertEqual(judge.requests, [])

    def test_an_unknown_scenario_has_no_card(self):
        with self.assertRaisesRegex(RubricError, "No rubric card"):
            card_for("bulletin-market-brief")


class UnscoredTests(unittest.TestCase):
    def test_a_failed_judge_is_unscored_and_never_a_zero(self):
        class BrokenJudge:
            mode = "broken"
            model = None

            def judge(self, request):
                raise TimeoutError("judge timed out")

        document = score(SUPPORT_REVIEW_PACKET, support_facts(), BrokenJudge())
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])
        self.assertIsNone(document["normalized_reward"])
        self.assertIn("judge timed out", document["judge_error"])
        self.assertEqual(document["deterministic_points"], 6.0)
        answers = {row["id"]: row["answer"] for row in document["criteria"]}
        self.assertIsNone(answers["usefulness"])
        self.assertNotIn("no", answers.values())

    def test_a_cancelled_dispatch_degrades_instead_of_escaping(self):
        class CancelledJudge:
            mode = "cancelled"
            model = None

            def judge(self, request):
                raise asyncio.CancelledError()

        document = score(SUPPORT_REVIEW_PACKET, support_facts(), CancelledJudge())
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])
        self.assertIn("CancelledError", document["judge_error"])

    def test_an_operator_interrupt_is_not_a_judge_fault(self):
        class InterruptedJudge:
            mode = "interrupted"
            model = None

            def judge(self, request):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            score(SUPPORT_REVIEW_PACKET, support_facts(), InterruptedJudge())

    def test_a_caller_mistake_raised_inside_the_judge_is_not_degraded(self):
        class ComplainingJudge:
            mode = "complaining"
            model = None

            def judge(self, request):
                raise RubricError("The request names a criterion this judge cannot answer")

        with self.assertRaisesRegex(RubricError, "cannot answer"):
            score(SUPPORT_REVIEW_PACKET, support_facts(), ComplainingJudge())

    def test_an_incomplete_or_mismatched_reply_keeps_its_llm_criteria_unanswered(self):
        incomplete = RecordedJudge({"usefulness": "yes"}, completeness="incomplete")
        document = score(SUPPORT_REVIEW_PACKET, support_facts(), incomplete)
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(document["judge_error"], "Judge reported incomplete evidence")
        self.assertEqual(document["judge"]["mode"], "recorded")  # the reply is still attributable
        self.assertIsNone([row for row in document["criteria"] if row["id"] == "usefulness"][0]["points"])

        partial = RecordedJudge({"usefulness": "yes", "honesty": "yes"})
        self.assertEqual(
            score(SUPPORT_REVIEW_PACKET, support_facts(), partial)["judge_error"], "Judge omitted a scored criterion"
        )
        unknown = RecordedJudge({"usefulness": "yes", "honesty": "yes", "actionability": "yes", "ledger": "no"})
        self.assertEqual(
            score(SUPPORT_REVIEW_PACKET, support_facts(), unknown)["judge_error"],
            "Judge answered an unknown criterion",
        )

    def test_a_reply_from_another_judge_prompt_or_run_is_not_awarded(self):
        class ForgedJudge(RecordedJudge):
            """Answers correctly, then rewrites the attribution that binds them."""

            def __init__(self, attribution, dropped=()):
                super().__init__({"usefulness": "yes", "honesty": "yes", "actionability": "yes"})
                self.forged, self.dropped = attribution, dropped

            def judge(self, request):
                reply = super().judge(request)
                kept = {k: v for k, v in reply.attribution.items() if k not in self.dropped}
                return JudgeReply(reply.answers, reply.reasons, {**kept, **self.forged}, reply.completeness)

        faults = [
            ("differs from the dispatched judge", ForgedJudge({"model": "gpt-6-astra"})),
            ("differs from the card prompt version", ForgedJudge({"prompt_version": "0.9"})),
            ("answered a different run", ForgedJudge({"run_digest": "another-run"})),
            ("attribution is incomplete", ForgedJudge({}, dropped=("response_digest",))),
        ]
        for fault, judge in faults:
            document = score(SUPPORT_REVIEW_PACKET, support_facts(), judge)
            with self.subTest(fault=fault):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertRegex(document["judge_error"], fault)

    def test_a_run_that_did_not_execute_is_unscored_rather_than_scored_low(self):
        document = score(SUPPORT_REVIEW_PACKET, support_facts(execution_pass=False), support_judge())
        self.assertEqual(document["status"], "unscored")
        self.assertIsNone(document["score_0_10"])
        self.assertIsNone(document["normalized_reward"])
        self.assertFalse(document["execution_pass"])
        self.assertIsNone(document["judge_error"])
        # Execution never multiplies a criterion: the resolved answers stay reported.
        answers = [row["answer"] for row in document["criteria"]]
        self.assertEqual(answers, ["yes", "yes", "yes", None, None, None])
        self.assertEqual(document["deterministic_points"], 6.0)


class ArithmeticTests(unittest.TestCase):
    def test_the_recorded_checkout_criteria_reproduce_their_published_total(self):
        document = score(CHECKOUT, CHECKOUT_FACTS, RecordedJudge(CHECKOUT_ANSWERS))
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["score_0_10"], 9.33)
        self.assertEqual(document["normalized_reward"], 0.933)
        self.assertEqual(document["deterministic_points"], 6.0)
        self.assertTrue(document["execution_pass"])
        self.assertEqual(document["answer_values"], {"yes": 1.0, "maybe": 0.33, "no": 0.0})
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertEqual(rows["outcome"]["points"], 3.0)
        self.assertEqual(rows["outcome"]["reason"], "Protected environment verification")
        self.assertEqual(rows["outcome"]["refs_supplied"], ["check-eur_fixed"])
        self.assertEqual(rows["communication"]["points"], 0.33)
        self.assertEqual(rows["communication"]["refs_supplied"], ["candidate-final"])
        self.assertEqual(rows["evidence"]["refs_supplied"], [])

    def test_the_reward_comes_off_the_same_decimal_as_the_score(self):
        document = score(
            LOSSY,
            RunFacts(True, {}, prose={"candidate": "draft"}),
            RecordedJudge({"single": "maybe"}),
        )
        self.assertEqual(document["score_0_10"], 3.30)
        # float(3.30) / 10 is 0.32999999999999996; the Decimal quotient is not.
        self.assertNotEqual(document["normalized_reward"], document["score_0_10"] / 10)
        self.assertEqual(document["normalized_reward"], 0.33)
        self.assertEqual(Decimal(str(document["normalized_reward"])), Decimal("0.33"))

    def test_a_failed_check_costs_its_weight_and_nothing_else(self):
        facts = RunFacts(
            execution_pass=True,
            checks={**CHECKOUT_FACTS.checks, "regressions": False},
            prose=CHECKOUT_FACTS.prose,
        )
        document = score(CHECKOUT, facts, RecordedJudge(CHECKOUT_ANSWERS))
        self.assertEqual(document["score_0_10"], 8.33)
        self.assertEqual(document["deterministic_points"], 5.0)
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertEqual((rows["regression"]["answer"], rows["regression"]["points"]), ("no", 0.0))

    def test_the_card_records_its_own_identity_and_origin(self):
        document = score(SUPPORT_REVIEW_PACKET, support_facts(), support_judge())
        self.assertEqual(
            document["rubric"],
            {
                "id": "support-review-packet",
                "version": "1.0.0",
                "origin": "local",
                "digest": rubric.digest(
                    {
                        "id": "support-review-packet",
                        "version": "1.0.0",
                        "answer_values": {"yes": 1.0, "maybe": 0.33, "no": 0.0},
                        "criteria": [
                            {
                                "id": criterion.id,
                                "question": criterion.question,
                                "weight": criterion.weight,
                                "evaluator": criterion.evaluator,
                                "check_id": criterion.check_id,
                                "anchors": dict(criterion.anchors),
                                "required_evidence": list(criterion.required_evidence),
                            }
                            for criterion in SUPPORT_REVIEW_PACKET.criteria
                        ],
                    }
                ),
            },
        )
        self.assertEqual(sum(criterion.weight for criterion in SUPPORT_REVIEW_PACKET.criteria), 10)
        self.assertEqual(set(CARDS), {"support-review-packet", "invoice-total", "dual-ledger-closeout"})
