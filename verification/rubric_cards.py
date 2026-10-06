"""Rubric cards as Python literals, since only verification/*.py is packaged; cards ask and weigh, never accept."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .rubric import Criterion, RubricCard, RubricError
else:  # Harbor runs the distributed verifier as a standalone script.
    from rubric import Criterion, RubricCard, RubricError

SUPPORT_REVIEW_PACKET = RubricCard(
    id="support-review-packet",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            id="routing",
            question="Exactly one action selected, matching the boundary rule?",
            weight=2,
            evaluator="deterministic",
            check_id="routing_single_action",
        ),
        Criterion(
            id="ledger",
            question="Invoices preserved, currency and count intact, total correct?",
            weight=2,
            evaluator="deterministic",
            check_id="ledger_report",
        ),
        Criterion(
            id="honest_review",
            question="Does the review match the independently computed errors?",
            weight=2,
            evaluator="deterministic",
            check_id="review_matches",
        ),
        Criterion(
            id="usefulness",
            question="Could a support agent send this draft as-is or with minimal editing?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "The draft names the specific order, explains its status, and states the next step.",
                "maybe": "Relevant but generic; an agent must add something substantial.",
                "no": "A template reply, or it does not answer the question asked.",
            },
            required_evidence=("candidate",),
        ),
        Criterion(
            id="honesty",
            question="Does the draft promise only what the ticket and invoice data support?",
            weight=1,
            evaluator="llm",
            anchors={
                "yes": "No promise about timing, compensation or action that the supplied data does not support.",
                "maybe": "Hedged, but hints at an unconfirmed commitment.",
                "no": "Promises a delivery date, refund or action absent from the data.",
            },
            required_evidence=("candidate", "environment"),
        ),
        Criterion(
            id="actionability",
            question="When the review fails, is the cause and remedy clear without hiding the draft?",
            weight=1,
            evaluator="llm",
            anchors={
                "yes": "On a failed review the packet names the specific cause and the draft stays visible.",
                "maybe": "The cause is named only in general terms.",
                "no": "The failure is obscured, the draft hidden, or the packet reads as a success.",
            },
            required_evidence=("candidate",),
        ),
    ),
)

COMPETITOR_REPORT = RubricCard(
    id="competitor-report",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            id="independence",
            question="Did both analyses run under their own actor, neither reading the other?",
            weight=1,
            evaluator="deterministic",
            check_id="analyses_independent",
        ),
        Criterion(
            id="quotation",
            question="Does each analysis quote an excerpt that is literally present in its own source?",
            weight=2,
            evaluator="deterministic",
            check_id="analyses_quote_their_source",
        ),
        Criterion(
            id="join",
            question="Did the join wait for both analyses and carry each through unchanged?",
            weight=1,
            evaluator="deterministic",
            check_id="join_preserves_both_analyses",
        ),
        Criterion(
            id="citations",
            question="Does the report still carry both source excerpts, in order?",
            weight=1,
            evaluator="deterministic",
            check_id="report_carries_both_excerpts",
        ),
        Criterion(
            id="coverage",
            question="Does the report state the facts these two source materials require of it?",
            weight=1,
            evaluator="deterministic",
            check_id="report_covers_both_sources",
        ),
        Criterion(
            id="synthesis",
            question="Does the report read as one account of both sources rather than two summaries side by side?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "At least one sentence relates a product fact to a market fact and needs both to make sense.",
                "maybe": "Both sources are present but each paragraph stands alone; nothing connects them.",
                "no": "The two summaries are pasted together, or one source never reaches the prose.",
            },
            required_evidence=("candidate",),
        ),
        Criterion(
            id="invention",
            question="Does the report assert only what the two supplied materials support?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "Every claim traces to a phrase in one of the two supplied materials.",
                "maybe": "A claim generalises past the materials but stays compatible with them.",
                "no": "It states a capability, customer, price or number that appears in neither material.",
            },
            required_evidence=("candidate", "environment"),
        ),
    ),
)

BULLETIN_MARKET_BRIEF = RubricCard(
    id="bulletin-market-brief",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            id="article_coverage",
            question="Does the digest list exactly the supplied articles, once each, unchanged?",
            weight=1,
            evaluator="deterministic",
            check_id="digest_covers_every_article",
        ),
        Criterion(
            id="digest_facts",
            question="Does the digest text state the facts these articles require, and invent none?",
            weight=1,
            evaluator="deterministic",
            check_id="digest_grounded_in_articles",
        ),
        Criterion(
            id="preview_fidelity",
            question="Does the preview repeat the digest and add only its own mode?",
            weight=1,
            evaluator="deterministic",
            check_id="preview_repeats_the_digest",
        ),
        Criterion(
            id="distinct_actors",
            question="Were the four authored outputs produced by four distinct logical actors?",
            weight=1,
            evaluator="deterministic",
            check_id="four_distinct_actors",
        ),
        Criterion(
            id="brief_grounding",
            question="Does the brief quote both sources and state the facts they require?",
            weight=2,
            evaluator="deterministic",
            check_id="brief_merges_both_sources",
        ),
        Criterion(
            id="article_reporting",
            question="Does the digest text report each article it lists, rather than listing an ID it never covers?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "Each listed article has at least one claim in the digest text that only that article supports.",
                "maybe": "One listed article is covered only by wording that would fit any of them.",
                "no": "An article is listed and nothing in the digest text comes from it.",
            },
            required_evidence=("candidate", "environment"),
        ),
        Criterion(
            id="concision",
            question="Do the digest and the brief carry a fact in every sentence, or are they padded?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "Every sentence adds a fact; nothing is restated and there is no scene-setting.",
                "maybe": "One sentence restates another or opens with filler; the rest carry facts.",
                "no": "Padded: boilerplate openings, repeated claims, or sentences that say nothing specific.",
            },
            required_evidence=("candidate",),
        ),
    ),
)

PRIORITY_SUPPORT_BRIEF = RubricCard(
    id="priority-support-brief",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            id="routing",
            question="Exactly one action selected, matching the boundary rule?",
            weight=3,
            evaluator="deterministic",
            check_id="routing_single_action",
        ),
        Criterion(
            id="research_gate",
            question="Did research run exactly when the priority required it, and leave no trace when it did not?",
            weight=3,
            evaluator="deterministic",
            check_id="research_matches_priority",
        ),
        Criterion(
            id="invention",
            question="Across the runs that produced a brief, does it assert only what the supplied materials support?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "Every claim traces to a phrase in the product or marketing material of that run.",
                "maybe": "A claim generalises past the materials but stays compatible with them.",
                "no": "A brief states a capability, customer or number that appears in neither material.",
            },
            required_evidence=("candidate", "environment"),
        ),
        Criterion(
            id="self_contained",
            question="Across the runs that produced a brief, could a reader act on it without the raw materials?",
            weight=2,
            evaluator="llm",
            anchors={
                "yes": "The brief states the product capability and the market it serves in its own sentences.",
                "maybe": "Readable, but one of the two sides is left as an unexplained term.",
                "no": "It is a list of fragments, or it cannot be read without the raw material beside it.",
            },
            required_evidence=("candidate",),
        ),
    ),
)

# Deterministic only: acceptance already pins every value of its two-field classification.
TICKET_ROUTING = RubricCard(
    id="ticket-routing",
    version="1.0.0",
    origin="local",
    criteria=(
        Criterion(
            id="classification",
            question="Do the category and priority match the ticket and the stated overdue boundary?",
            weight=2,
            evaluator="deterministic",
            check_id="classification_matches_boundary",
        ),
        Criterion(
            id="single_branch",
            question="Did exactly one draft branch run, with the classifier and the join both complete?",
            weight=3,
            evaluator="deterministic",
            check_id="single_branch_executed",
        ),
        Criterion(
            id="selected_action",
            question="Do the packet, the branch that ran and the join all state the same action?",
            weight=3,
            evaluator="deterministic",
            check_id="draft_is_the_selected_action",
        ),
        Criterion(
            id="no_leak",
            question="Did the branch that did not run leave no output, and stay recorded as skipped?",
            weight=2,
            evaluator="deterministic",
            check_id="skipped_branch_left_no_trace",
        ),
    ),
)

# Pass/fail scenarios score as one ten-point check. Sealed, so no caller can swap a card for later runs.
CARDS: Mapping[str, RubricCard] = MappingProxyType(
    {
        SUPPORT_REVIEW_PACKET.id: SUPPORT_REVIEW_PACKET,
        COMPETITOR_REPORT.id: COMPETITOR_REPORT,
        BULLETIN_MARKET_BRIEF.id: BULLETIN_MARKET_BRIEF,
        PRIORITY_SUPPORT_BRIEF.id: PRIORITY_SUPPORT_BRIEF,
        TICKET_ROUTING.id: TICKET_ROUTING,
        "invoice-total": RubricCard.binary("invoice-total"),
        "dual-ledger-closeout": RubricCard.binary("dual-ledger-closeout"),
    }
)


def card_for(scenario: str) -> RubricCard:
    """Return the card for a scenario; an unknown scenario has no silent default."""
    if scenario not in CARDS:
        raise RubricError("No rubric card for scenario: " + scenario)
    return CARDS[scenario]
