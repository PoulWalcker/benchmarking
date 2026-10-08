"""Small rubric data shared by scoring tests, independent of benchmark discovery."""

from verification.rubric import Criterion, RubricCard

ANCHORS = {"yes": "Supported.", "maybe": "Partly supported.", "no": "Unsupported."}
CARD = RubricCard(
    "rubric-sample",
    "1.0.0",
    "local",
    (
        Criterion("first", "Did the first obligation hold?", 2, "deterministic", "first_check"),
        Criterion("second", "Did the second obligation hold?", 2, "deterministic", "second_check"),
        Criterion("third", "Did the third obligation hold?", 2, "deterministic", "third_check"),
        Criterion(
            "usefulness", "Is the explanation useful?", 2, "llm", anchors=ANCHORS, required_evidence=("candidate",)
        ),
        Criterion(
            "honesty",
            "Is the explanation supported?",
            1,
            "llm",
            anchors=ANCHORS,
            required_evidence=("environment", "candidate"),
        ),
        Criterion(
            "actionability",
            "Is the explanation actionable?",
            1,
            "llm",
            anchors=ANCHORS,
            required_evidence=("candidate",),
        ),
    ),
)
