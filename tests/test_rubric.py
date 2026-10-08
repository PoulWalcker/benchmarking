"""Rubric scoring tests use fabricated run facts and never reach a model."""

import ast
import asyncio
from decimal import ROUND_HALF_UP, Decimal
import inspect
import math
import unittest

from sapi_config_lab.evaluate import autowfbench
from sapi_config_lab.paths import workspace_root
from tests.support.rubric import CARD as SAMPLE_CARD
from verification import rubric, rubric_cards
from verification.rubric import (
    Criterion,
    JudgeReply,
    RecordedJudge,
    RubricCard,
    RubricError,
    RunFacts,
    score,
)
from verification.rubric_cards import card_for

ANCHORS = {"yes": "a", "maybe": "b", "no": "c"}

CARDED = {"invoice-total"}
# The scenarios that ship a card file today.
CARDS = {
    path.parent.parent.name.split("-", 1)[1]
    for path in (workspace_root() / "benchmarks").glob("*/evaluation/rubric.json")
}


# The criteria of the 2026-10-05 checkout recovery run as the upstream judge scored them (9.33/10).
# Evidence sources follow this module's rule that no llm criterion reads protected verification.
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


# Two deterministic points and one "maybe" over three weights is 23.30/3, the
# smallest card whose exact quotient needs rounding at the second decimal: 7.77
# half-up against 7.76 truncated. Every other fixture here lands exactly on two
# decimals, which left both the rounding mode and the precision untested.
ROUNDING = RubricCard(
    id="rounding",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion("first", "Did the first check pass?", 1, "deterministic", "first_check"),
        Criterion("second", "Did the second check pass?", 1, "deterministic", "second_check"),
        Criterion("judged", "Is the draft usable?", 1, "llm", anchors=ANCHORS, required_evidence=("candidate",)),
    ),
)
ROUNDING_FACTS = RunFacts(
    execution_pass=True,
    checks={"first_check": True, "second_check": True},
    prose={"candidate": "draft"},
)


def sample_facts(**overrides):
    facts = {
        "execution_pass": True,
        "checks": {"first_check": True, "second_check": True, "third_check": True},
        "prose": {
            "candidate": "sample explanation",
            "environment": "sample inputs",
            "verification": "protected checks",
        },
        "refs": {"usefulness": ("candidate-final",)},
        "run_digest": "sample-0001",
    }
    facts.update(overrides)
    return RunFacts(**facts)


def sample_judge(**overrides):
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

    def test_the_module_imports_nothing_that_could_touch_a_file_or_the_environment(self):
        # Guards the docstring's purity claim itself. The surface test above sees
        # public names only, so `from os import environ as _environ` walked past
        # it while the module went on promising no file and no environment read.
        for module, allowed in (
            (
                rubric,
                {
                    "__future__",
                    "asyncio",
                    "collections.abc",
                    "dataclasses",
                    "decimal",
                    "hashlib",
                    "json",
                    "types",
                    "typing",
                },
            ),
            (rubric_cards, {"__future__", "json", "typing", ".contracts", "contracts", ".rubric", "rubric"}),
        ):
            with self.subTest(module=module.__name__):
                self.assertEqual(_imported_modules(module), allowed)

    def test_the_canonical_digest_refuses_a_value_json_cannot_round_trip(self):
        # allow_nan=True would emit bare NaN, which no other JSON reader accepts.
        for sample in (float("nan"), float("inf"), {"a": float("-inf")}, [float("nan")]):
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                rubric.digest(sample)

    def test_the_document_claims_its_own_schema_not_the_autowfbench_one(self):
        document = score(SAMPLE_CARD, sample_facts(), sample_judge())
        self.assertEqual(document["schema"], "sapi-lab-rubric-evaluation/v1")
        self.assertNotEqual(document["schema"], autowfbench.SCHEMA)

    def test_the_canonical_digest_agrees_with_the_upstream_adapter(self):
        for sample in ({"b": 1, "a": [2, None, True]}, {"text": "café — résumé"}, [], 0.33):
            with self.subTest(sample=sample):
                self.assertEqual(rubric.digest(sample), autowfbench.digest(sample))


def _imported_modules(module):
    """Every module name the source imports, aliased or not, public or private."""
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


class RewardInvariantTests(unittest.TestCase):
    def test_no_scored_document_carries_a_harbor_reward_payload(self):
        # Every oracle/nop control gate asserts {"reward": 1.0} or {"reward": 0.0}
        # exactly, so nothing this module emits may be mistaken for that payload.
        documents = [
            score(SAMPLE_CARD, sample_facts(), sample_judge(actionability="maybe")),
            score(SAMPLE_CARD, sample_facts(), RecordedJudge({})),
            score(SAMPLE_CARD, sample_facts(execution_pass=False), sample_judge()),
            score(card_for("invoice-total"), RunFacts(True, {"accepted": True})),
            score(card_for("invoice-total"), RunFacts(True, {"accepted": False})),
        ]
        # Matching "reward" and "rewards" by exact name let `harbor_reward` or
        # `reward_0_1` walk past, so every key naming a reward at all is listed.
        for document in documents:
            with self.subTest(status=document["status"]):
                self.assertEqual(
                    {key for key in _every_key(document) if "reward" in key.lower()},
                    {"normalized_reward"},
                )
                reward = document["normalized_reward"]
                self.assertTrue(reward is None or 0.0 <= reward <= 1.0)
                self.assertTrue(document["score_0_10"] is None or 0.0 <= document["score_0_10"] <= 10.0)

    def test_binary_cards_keep_the_existing_pass_fail_behaviour(self):
        for scenario in ("invoice-total",):
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
    # Every container, not dict and list only: a tuple- or set-valued field hid
    # whatever keys it carried from the reward guard above.
    if isinstance(value, (list, tuple, set, frozenset)):
        return {key for item in value for key in _every_key(item)}
    return set()


class EveryKeyTests(unittest.TestCase):
    def test_the_reward_guard_sees_through_every_container(self):
        self.assertEqual(_every_key({"a": ({"reward": 1},)}), {"a", "reward"})
        self.assertEqual(_every_key({"a": [{"b": {"reward": 1}}]}), {"a", "b", "reward"})
        self.assertEqual(_every_key({"a": frozenset()}), {"a"})


class DispatchTests(unittest.TestCase):
    def test_a_deterministic_card_never_touches_a_judge_that_was_passed(self):
        judge = RecordedJudge({"usefulness": "yes"})
        document = score(card_for("invoice-total"), RunFacts(True, {"accepted": True}), judge)
        self.assertEqual(judge.requests, [])
        self.assertIsNone(document["judge"])
        self.assertIsNone(document["judge_error"])
        self.assertEqual(document["status"], "complete")

    def test_one_run_costs_at_most_one_dispatch(self):
        judge = sample_judge()
        score(SAMPLE_CARD, sample_facts(), judge)
        self.assertEqual(len(judge.requests), 1)

    def test_a_non_executing_run_is_never_charged_for_a_judgement_it_discards(self):
        judge = sample_judge()
        document = score(SAMPLE_CARD, sample_facts(execution_pass=False), judge)
        self.assertEqual(judge.requests, [])
        self.assertEqual(document["status"], "unscored")
        self.assertIsNone(document["judge_error"])
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertIsNone(rows["usefulness"]["answer"])
        self.assertEqual(rows["usefulness"]["reason"], "No judge was asked: the run did not execute")

    def test_a_judge_that_answers_nothing_is_a_judge_fault(self):
        self.assertEqual(
            score(SAMPLE_CARD, sample_facts(), RecordedJudge({}))["status"],
            "judge_failed",
        )

    def test_a_non_executing_run_can_carry_no_judge_fault(self):
        # The two statuses never compete: nothing is dispatched for a run that
        # did not execute, so there is no fault for "unscored" to hide.
        class ExplodingJudge:
            mode = "exploding"
            model = None

            def judge(self, request):
                raise AssertionError("dispatched for a run that did not execute")

        document = score(SAMPLE_CARD, sample_facts(execution_pass=False), ExplodingJudge())
        self.assertEqual(document["status"], "unscored")
        self.assertIsNone(document["judge_error"])


class JudgeVisibilityTests(unittest.TestCase):
    def test_the_judge_receives_no_execution_flag_and_no_checks_at_all(self):
        judge = sample_judge()
        score(SAMPLE_CARD, sample_facts(), judge)
        facts = judge.requests[0].facts
        # Withheld means absent. A False flag would assert the run did not execute.
        self.assertFalse(hasattr(facts, "execution_pass"))
        self.assertFalse(hasattr(facts, "checks"))

    def test_the_judge_never_sees_the_protected_verification_narrative(self):
        judge = sample_judge()
        score(SAMPLE_CARD, sample_facts(), judge)
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
            score(card, RunFacts(True, {}, prose={"verification": "x"}), sample_judge())

    def test_the_judge_receives_only_the_criteria_it_must_answer(self):
        judge = sample_judge()
        score(SAMPLE_CARD, sample_facts(), judge)
        request = judge.requests[0]
        self.assertEqual(
            tuple(criterion.id for criterion in request.criteria), ("usefulness", "honesty", "actionability")
        )
        # No deterministic check name or question crosses the seam.
        self.assertEqual([criterion.check_id for criterion in request.criteria], ["", "", ""])
        self.assertEqual(request.card_digest, SAMPLE_CARD.digest())

    def test_refs_keyed_by_a_deterministic_criterion_never_reach_the_judge(self):
        # Prose is only half the boundary: refs are keyed by criterion id, and
        # unfiltered they hand the judge the references behind a check it may
        # not see at all.
        judge = sample_judge()
        refs = {"usefulness": ("candidate-final",), "first": ("check-first-trace",), "second": ("checks.json",)}
        score(SAMPLE_CARD, sample_facts(refs=refs), judge)
        self.assertEqual(set(judge.requests[0].facts.refs), {"usefulness"})

    def test_judged_rows_do_not_claim_to_hold_judge_citations(self):
        document = score(SAMPLE_CARD, sample_facts(), sample_judge())
        rows = {row["id"]: row for row in document["criteria"]}
        self.assertNotIn("evidence_refs", rows["usefulness"])
        self.assertEqual(rows["usefulness"]["refs_supplied"], ["candidate-final"])
        self.assertEqual(rows["first"]["refs_supplied"], ["check-first_check"])


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

        before = SAMPLE_CARD.digest()
        document = score(SAMPLE_CARD, sample_facts(), MutatingJudge({"usefulness": "yes"}))
        self.assertEqual(len(refused), 3)  # the write is refused, not merely undone
        self.assertEqual(SAMPLE_CARD.digest(), before)
        self.assertEqual(document["rubric"]["digest"], before)
        self.assertEqual(
            SAMPLE_CARD.criteria[3].anchors["maybe"],
            "Partly supported.",
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
        prose, refs = {"candidate": "draft"}, {"judged": ["candidate-final"]}
        facts = RunFacts(execution_pass=True, checks=checks, prose=prose, refs=refs)
        checks["accepted"] = False
        prose["candidate"] = "rewritten"
        prose["verification"] = "leaked"
        refs["judged"].append("extra")
        refs["routing"] = ("extra",)
        self.assertEqual(score(card_for("invoice-total"), facts)["score_0_10"], 10.0)
        self.assertEqual(dict(facts.prose), {"candidate": "draft"})
        self.assertEqual(dict(facts.refs), {"judged": ("candidate-final",)})

    def test_a_judge_reply_keeps_the_mappings_it_was_handed(self):
        answers, reasons, attribution = {"q": "yes"}, {"q": "because"}, {"mode": "recorded"}
        reply = JudgeReply(answers, reasons, attribution)
        answers["q"] = "no"
        reasons["q"] = "rewritten"
        attribution["mode"] = "forged"
        self.assertEqual((dict(reply.answers), dict(reply.reasons)), ({"q": "yes"}, {"q": "because"}))
        self.assertEqual(dict(reply.attribution), {"mode": "recorded"})
        for mapping in (reply.answers, reply.reasons, reply.attribution):
            with self.subTest(mapping=mapping), self.assertRaises(TypeError):
                mapping["q"] = "written through"

    def test_a_card_built_from_a_list_stops_following_that_list(self):
        criteria = [Criterion("a", "?", 1, "deterministic", "accepted")]
        card = RubricCard("t", "1.0.0", "local", criteria)
        before = card.digest()
        criteria.append(Criterion("b", "?", 9, "deterministic", "other"))
        self.assertEqual(len(card.criteria), 1)
        self.assertEqual(card.digest(), before)
        self.assertEqual(score(card, RunFacts(True, {"accepted": True}))["score_0_10"], 10.0)

    def test_references_given_as_one_string_are_refused_not_spread(self):
        # tuple("abc") is ("a", "b", "c"); a silently exploded reference list is
        # not a reference list.
        with self.assertRaisesRegex(RubricError, "not one string"):
            RunFacts(True, {}, refs={"judged": "candidate-final"})
        self.assertEqual(RunFacts(True, {}, refs={"judged": ["x"]}).refs["judged"], ("x",))

    def test_a_card_is_read_from_scenario_data_and_cannot_be_altered_in_process(self):
        # A card held in a mutable registry could be swapped for every later run; one read from
        # the scenario's rubric.json into a frozen dataclass cannot.
        card = card_for("invoice-total")
        with self.assertRaises(AttributeError):
            card.id = "evil"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            card.answer_values["yes"] = 0.0  # type: ignore[index]
        self.assertEqual(card_for("invoice-total").digest(), card.digest())
        self.assertEqual(CARDS, CARDED)


class CardRewriteTests(unittest.TestCase):
    """A judge holds the card's own Criterion objects; object.__setattr__ beats frozen."""

    def card(self):
        return RubricCard(
            "rewrite",
            "1.0.0",
            "local",
            (
                Criterion("checked", "?", 2, "deterministic", "accepted"),
                Criterion("one", "?", 1, "llm", anchors=ANCHORS, required_evidence=("candidate",)),
                Criterion("two", "?", 1, "llm", anchors=ANCHORS, required_evidence=("candidate",)),
            ),
        )

    def facts(self):
        return RunFacts(True, {"accepted": True}, prose={"candidate": "draft"}, run_digest="rewrite-1")

    def rewriting(self, field, value):
        class RewritingJudge(RecordedJudge):
            def judge(self, request):
                object.__setattr__(request.criteria[0], field, value)
                return super().judge(request)

        return RewritingJudge({"one": "yes", "two": "yes"})

    def test_a_rewritten_weight_is_a_judge_fault_and_never_a_score(self):
        for value in (-5, 0, 10**6):
            card, judge = self.card(), self.rewriting("weight", value)
            document = score(card, self.facts(), judge)
            with self.subTest(weight=value):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertIsNone(document["normalized_reward"])
                self.assertRegex(document["judge_error"], "card changed during dispatch")
                # Not one point, including the deterministic ones, off a card
                # the recorded digest no longer describes.
                self.assertEqual(document["deterministic_points"], 0.0)
                self.assertEqual([row["points"] for row in document["criteria"]], [None, None, None])
                self.assertEqual([row["answer"] for row in document["criteria"]], [None, None, None])
                for row in document["criteria"]:
                    self.assertEqual(row["reason"], "Not scored: the rubric card changed while the judge held it")

    def test_zeroed_weights_degrade_instead_of_dividing_by_zero(self):
        class ZeroingJudge(RecordedJudge):
            def judge(self, request):
                for criterion in request.criteria:
                    object.__setattr__(criterion, "weight", 0)
                return super().judge(request)

        card = RubricCard(
            "all-llm",
            "1.0.0",
            "local",
            (Criterion("one", "?", 1, "llm", anchors=ANCHORS, required_evidence=("candidate",)),),
        )
        document = score(card, self.facts(), ZeroingJudge({"one": "yes"}))
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])

    def test_rewritten_evidence_an_evaluator_or_an_anchor_is_a_judge_fault(self):
        cases = {
            "required_evidence": ("verification",),
            "evaluator": "deterministic",
            "check_id": "accepted",
            "question": "a different question",
            "anchors": {"yes": "rewritten", "maybe": "b", "no": "c"},
        }
        for field, value in cases.items():
            document = score(self.card(), self.facts(), self.rewriting(field, value))
            with self.subTest(field=field):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertRegex(document["judge_error"], "card changed during dispatch")

    def test_an_untouched_card_still_scores(self):
        document = score(self.card(), self.facts(), RecordedJudge({"one": "yes", "two": "yes"}))
        self.assertEqual((document["status"], document["score_0_10"]), ("complete", 10.0))


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
        judge = sample_judge()
        card = RubricCard(
            "t",
            "1.0.0",
            "local",
            SAMPLE_CARD.criteria,
            answer_values={"yes": float("nan"), "maybe": 0.33, "no": 0.0},
        )
        with self.assertRaises(RubricError):
            score(card, sample_facts(), judge)
        self.assertEqual(judge.requests, [])


class ValidationTests(unittest.TestCase):
    def test_a_missing_deterministic_check_is_an_error_not_a_no(self):
        with self.assertRaisesRegex(RubricError, "Missing deterministic check: second_check"):
            score(SAMPLE_CARD, sample_facts(checks={"first_check": True, "third_check": True}))
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
                score(card, RunFacts(True, {"check_a": True, "check_b": True}), sample_judge())
        with self.assertRaisesRegex(RubricError, "sum above zero"):
            score(RubricCard("t", "1.0.0", "local", ()), RunFacts(True, {}))
        with self.assertRaisesRegex(RubricError, "execution_pass must be a boolean"):
            score(card_for("invoice-total"), RunFacts(1, {"accepted": True}))

    def test_a_weight_of_the_wrong_type_is_a_rubric_error_not_a_type_error(self):
        # Summing the weights before type-checking them raised TypeError out of
        # score(), which promises RubricError for a card it cannot use.
        for weight in ("2", 2.0, None, True, Decimal(2)):
            card = RubricCard("t", "1.0.0", "local", (Criterion("a", "?", weight, "deterministic", "accepted"),))
            with self.subTest(weight=weight), self.assertRaisesRegex(RubricError, "positive integer"):
                score(card, RunFacts(True, {"accepted": True}))

    def test_an_llm_card_requires_a_judge(self):
        with self.assertRaisesRegex(RubricError, "pass a judge"):
            score(SAMPLE_CARD, sample_facts())

    def test_required_evidence_must_be_satisfiable_before_a_judge_is_asked(self):
        judge = sample_judge()
        with self.assertRaisesRegex(RubricError, "honesty lacks: environment"):
            score(SAMPLE_CARD, sample_facts(prose={"candidate": "d"}), judge)
        self.assertEqual(judge.requests, [])

    def test_an_unknown_scenario_has_no_card(self):
        # An unknown name has no fallback card.
        for scenario in ("not-a-scenario",):
            with self.subTest(scenario=scenario), self.assertRaisesRegex(RubricError, "No rubric card"):
                card_for(scenario)


def _every_reason(document):
    return [row["reason"] for row in document["criteria"]]


class UnscoredTests(unittest.TestCase):
    def test_a_failed_judge_is_unscored_and_never_a_zero(self):
        class BrokenJudge:
            mode = "broken"
            model = None

            def judge(self, request):
                raise TimeoutError("judge timed out")

        document = score(SAMPLE_CARD, sample_facts(), BrokenJudge())
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

        document = score(SAMPLE_CARD, sample_facts(), CancelledJudge())
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])
        self.assertIn("CancelledError", document["judge_error"])

    def test_a_dispatch_cancelled_inside_a_task_group_degrades_instead_of_escaping(self):
        # asyncio.TaskGroup and asyncio.timeout raise BaseExceptionGroup, which
        # is not an Exception, so it slipped past the arm written for exactly
        # this case.
        class GroupedJudge:
            mode = "grouped"
            model = None

            def __init__(self, error):
                self.error = error

            def judge(self, request):
                raise BaseExceptionGroup("unhandled errors in a TaskGroup", [self.error])

        # A group holding a CancelledError is a BaseExceptionGroup and is not an
        # Exception; one holding a TimeoutError is an ExceptionGroup and is.
        for error, group in (
            (asyncio.CancelledError(), "BaseExceptionGroup"),
            (TimeoutError("late"), "ExceptionGroup"),
        ):
            document = score(SAMPLE_CARD, sample_facts(), GroupedJudge(error))
            with self.subTest(error=type(error).__name__):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertIn(group, document["judge_error"])
                self.assertIn("TaskGroup", document["judge_error"])

    def test_operator_intent_and_a_caller_mistake_keep_their_meaning_inside_a_group(self):
        class GroupedJudge:
            mode = "grouped"
            model = None

            def __init__(self, error):
                self.error = error

            def judge(self, request):
                raise BaseExceptionGroup("unhandled errors in a TaskGroup", [self.error])

        for error in (KeyboardInterrupt(), SystemExit(), RubricError("the request is unusable")):
            with self.subTest(error=type(error).__name__), self.assertRaises(BaseExceptionGroup):
                score(SAMPLE_CARD, sample_facts(), GroupedJudge(error))

    def test_a_reply_whose_mappings_are_not_mappings_degrades(self):
        # Judge is a Protocol and JudgeReply is not enforced, so `reasons=None`
        # reached dict() outside every try and escaped as TypeError.
        class DuckReply:
            def __init__(self, **fields):
                self.__dict__.update(fields)

        class DuckJudge(RecordedJudge):
            def __init__(self, broken):
                super().__init__({"usefulness": "yes", "honesty": "yes", "actionability": "yes"})
                self.broken = broken

            def judge(self, request):
                reply = super().judge(request)
                fields = {
                    "answers": dict(reply.answers),
                    "reasons": dict(reply.reasons),
                    "attribution": dict(reply.attribution),
                    "completeness": reply.completeness,
                }
                fields.update(self.broken)
                return DuckReply(**fields)

        for broken, fault in (
            ({"reasons": None}, "reasons is not a mapping"),
            ({"reasons": [("usefulness", "ok")]}, "reasons is not a mapping"),
            ({"answers": None}, "answers is not a mapping"),
            ({"attribution": None}, "attribution is not a mapping"),
            ({"attribution": [("mode", "recorded")]}, "attribution is not a mapping"),
        ):
            document = score(SAMPLE_CARD, sample_facts(), DuckJudge(broken))
            with self.subTest(broken=sorted(broken)):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertRegex(document["judge_error"], fault)

    def test_an_answer_outside_the_table_is_refused_rather_than_looked_up(self):
        for answer in ("probably", "YES", "", None, 1):
            judge = RecordedJudge({"usefulness": answer, "honesty": "yes", "actionability": "yes"})
            document = score(SAMPLE_CARD, sample_facts(), judge)
            with self.subTest(answer=answer):
                self.assertEqual(document["judge_error"], "Judge returned an unsupported answer")
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])

    def test_an_unanswered_row_says_what_happened_rather_than_promising_a_retry(self):
        # "Awaiting LLM judge" beside status judge_failed named a wait that does
        # not exist: one dispatch, no loop, no retry.
        class BrokenJudge:
            mode = "broken"
            model = None

            def judge(self, request):
                raise TimeoutError("judge timed out")

        document = score(SAMPLE_CARD, sample_facts(), BrokenJudge())
        judged = [row["reason"] for row in document["criteria"] if row["evaluator"] == "llm"]
        self.assertEqual(judged, ["The judge was asked once and returned no usable answer"] * 3)
        for reason in _every_reason(document):
            self.assertNotIn("await", reason.lower())

    def test_an_operator_interrupt_is_not_a_judge_fault(self):
        class InterruptedJudge:
            mode = "interrupted"
            model = None

            def judge(self, request):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            score(SAMPLE_CARD, sample_facts(), InterruptedJudge())

    def test_a_caller_mistake_raised_inside_the_judge_is_not_degraded(self):
        class ComplainingJudge:
            mode = "complaining"
            model = None

            def judge(self, request):
                raise RubricError("The request names a criterion this judge cannot answer")

        with self.assertRaisesRegex(RubricError, "cannot answer"):
            score(SAMPLE_CARD, sample_facts(), ComplainingJudge())

    def test_an_incomplete_or_mismatched_reply_keeps_its_llm_criteria_unanswered(self):
        incomplete = RecordedJudge({"usefulness": "yes"}, completeness="incomplete")
        document = score(SAMPLE_CARD, sample_facts(), incomplete)
        self.assertEqual(document["status"], "judge_failed")
        self.assertIsNone(document["score_0_10"])
        self.assertEqual(document["judge_error"], "Judge reported incomplete evidence")
        self.assertEqual(document["judge"]["mode"], "recorded")  # the reply is still attributable
        self.assertIsNone(next(row for row in document["criteria"] if row["id"] == "usefulness")["points"])

        partial = RecordedJudge({"usefulness": "yes", "honesty": "yes"})
        self.assertEqual(score(SAMPLE_CARD, sample_facts(), partial)["judge_error"], "Judge omitted a scored criterion")
        unknown = RecordedJudge({"usefulness": "yes", "honesty": "yes", "actionability": "yes", "ledger": "no"})
        self.assertEqual(
            score(SAMPLE_CARD, sample_facts(), unknown)["judge_error"],
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
            document = score(SAMPLE_CARD, sample_facts(), judge)
            with self.subTest(fault=fault):
                self.assertEqual(document["status"], "judge_failed")
                self.assertIsNone(document["score_0_10"])
                self.assertRegex(document["judge_error"], fault)

    def test_a_run_that_did_not_execute_is_unscored_rather_than_scored_low(self):
        document = score(SAMPLE_CARD, sample_facts(execution_pass=False), sample_judge())
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

    def test_the_score_rounds_half_up_at_exactly_two_decimals(self):
        # 23.30/3 is 7.7666…; half-up gives 7.77, truncation 7.76, three decimals
        # 7.767 and one decimal 7.8. Every other fixture lands exactly on two
        # decimals, so neither the mode nor the precision was observable.
        document = score(ROUNDING, ROUNDING_FACTS, RecordedJudge({"judged": "maybe"}))
        self.assertEqual(document["status"], "complete")
        self.assertEqual(document["score_0_10"], 7.77)
        self.assertEqual(Decimal(str(document["score_0_10"])), Decimal("7.77"))
        self.assertEqual(document["normalized_reward"], 0.777)
        self.assertEqual(Decimal(str(document["normalized_reward"])), Decimal("0.777"))
        self.assertEqual(document["deterministic_points"], 2.0)

    def test_naive_float_division_disagrees_for_the_count_the_module_states(self):
        # Pins the number in score()'s comment so it cannot rot: 289 of the 1001
        # reachable scores divide differently as a float than as a Decimal.
        disagreements = 0
        for hundredths in range(1001):
            exact = (Decimal(hundredths) / Decimal(100)).quantize(Decimal("0.01"))
            reward = float((exact / Decimal(10)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))
            disagreements += float(exact) / 10 != reward
        self.assertEqual(disagreements, 289)

    def test_points_come_from_the_canonical_table_not_the_cards_copy(self):
        # A card may state "no" as -0.0 and still be canonical numerically, so a
        # total taken from its floats signs the zero that the table does not.
        card = RubricCard(
            "signed",
            "1.0.0",
            "local",
            card_for("invoice-total").criteria,
            answer_values={"yes": 1.0, "maybe": 0.33, "no": -0.0},
        )
        document = score(card, RunFacts(True, {"accepted": False}))
        self.assertEqual(document["status"], "complete")
        for value in (document["score_0_10"], document["normalized_reward"], document["deterministic_points"]):
            self.assertEqual(math.copysign(1.0, value), 1.0)
        self.assertEqual(math.copysign(1.0, document["criteria"][0]["points"]), 1.0)
        self.assertEqual(document["answer_values"], {"yes": 1.0, "maybe": 0.33, "no": 0.0})

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
        document = score(SAMPLE_CARD, sample_facts(), sample_judge())
        self.assertEqual(
            document["rubric"],
            {
                "id": "rubric-sample",
                "version": "1.0.0",
                "origin": "local",
                "digest": rubric.digest(
                    {
                        "id": "rubric-sample",
                        "version": "1.0.0",
                        "origin": "local",
                        "prompt_version": rubric.PROMPT_VERSION,
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
                            for criterion in SAMPLE_CARD.criteria
                        ],
                    }
                ),
            },
        )
        self.assertEqual(sum(criterion.weight for criterion in SAMPLE_CARD.criteria), 10)
        self.assertEqual(set(CARDS), CARDED)

    def test_every_card_weighs_ten_with_a_deterministic_majority(self):
        """Ten points per card, and no card leaves the majority to a judge."""
        for scenario in sorted(CARDS):
            card = card_for(scenario)
            with self.subTest(scenario=scenario):
                self.assertEqual(card.id, scenario)
                total = sum(criterion.weight for criterion in card.criteria)
                determined = sum(
                    criterion.weight for criterion in card.criteria if criterion.evaluator == "deterministic"
                )
                self.assertEqual(total, 10)
                self.assertGreaterEqual(determined, total - determined)
                self.assertEqual(card.version, "1.0.0")
                self.assertEqual(card.origin, "local")

    def test_the_digest_covers_every_field_the_document_prints_beside_it(self):
        # The document prints `origin` next to the digest and the attribution
        # binds `prompt_version`; neither was covered, so an upstream and a
        # local card with the same questions shared one digest.
        criteria = (Criterion("a", "?", 1, "deterministic", "accepted"),)
        base = RubricCard("t", "1.0.0", "local", criteria)
        for other in (
            RubricCard("t", "1.0.0", "upstream", criteria),
            RubricCard("t", "1.0.0", "local", criteria, prompt_version="0.9"),
            RubricCard("t", "1.0.1", "local", criteria),
            RubricCard("u", "1.0.0", "local", criteria),
        ):
            with self.subTest(other=(other.id, other.version, other.origin, other.prompt_version)):
                self.assertNotEqual(base.digest(), other.digest())
        self.assertEqual(base.digest(), RubricCard("t", "1.0.0", "local", criteria).digest())
