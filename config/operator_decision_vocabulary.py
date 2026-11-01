#!/usr/bin/env python3
"""Single Source of Truth (SSOT) vocabulary for Operator Decisions and Human-in-the-Loop governance.

Disambiguates the overloaded legacy "HITL" nomenclature into six distinct,
non-overlapping architectural concepts:

1. Channel: ask_user_question
   The interactive tool/channel through which multiple-choice questions are presented
   to the operator. It is a communication vehicle, not a rule.

2. Ambiguity Rule: needs_operator_decision
   The algorithmic test determining whether operator adjudication is mandatory
   (confidence gap <= 20% between top two options; > 20% resolves autonomously).

3. Tool Guard: TOOL_APPROVAL_REQUIRED
   A binary permission / approval check before invoking an expensive, risky, or
   destructive tool. It is not a decision between options.

4. Terminal Run Outcome: OPERATOR_INPUT_REQUIRED
   The terminal status emitted when an unattended or scheduled pipeline run stops
   because an unresolved decision requires operator input.

5. Persistent Store: decision_record
   An immutable entry recorded in the persistent ledger (hitl_decision_ledger)
   capturing the operator's verified selection.

6. Cryptographic Proof: approval_signature_valid
   Cryptographic Ed25519 signature / Merkle-bound receipt attesting that an approval
   or decision originated from an authenticated human operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Optional

# ==============================================================================
# CANONICAL VOCABULARY CONSTANTS
# ==============================================================================

ASK_USER_QUESTION: str = "ask_user_question"
NEEDS_OPERATOR_DECISION: str = "needs_operator_decision"
TOOL_APPROVAL_REQUIRED: str = "TOOL_APPROVAL_REQUIRED"
OPERATOR_INPUT_REQUIRED: str = "OPERATOR_INPUT_REQUIRED"
DECISION_RECORD: str = "decision_record"
APPROVAL_SIGNATURE_VALID: str = "approval_signature_valid"


class OperatorDecisionConcept(str, Enum):
    """Enumeration of the six canonical operator decision concepts."""

    CHANNEL = ASK_USER_QUESTION
    AMBIGUITY_RULE = NEEDS_OPERATOR_DECISION
    TOOL_GUARD = TOOL_APPROVAL_REQUIRED
    RUN_OUTCOME = OPERATOR_INPUT_REQUIRED
    PERSISTENT_RECORD = DECISION_RECORD
    APPROVAL_PROOF = APPROVAL_SIGNATURE_VALID


CANONICAL_VOCABULARY_TERMS: FrozenSet[str] = frozenset(
    {
        ASK_USER_QUESTION,
        NEEDS_OPERATOR_DECISION,
        TOOL_APPROVAL_REQUIRED,
        OPERATOR_INPUT_REQUIRED,
        DECISION_RECORD,
        APPROVAL_SIGNATURE_VALID,
    }
)


# ==============================================================================
# DEPRECATED TERMS & DISAMBIGUATION MAPPINGS
# ==============================================================================

DEPRECATED_TERMS: FrozenSet[str] = frozenset(
    {
        "ask_question",
        "ask_user",
        "requires_hitl",
        "HITL_REQUIRED_TOOLS",
        "HITL_REQUIRED",
        "hitl_required",
        "hitl_receipt_bound",
    }
)

# Mapping from deprecated/overloaded legacy term to canonical concept
DISAMBIGUATION_MAP: Dict[str, str] = {
    "ask_question": ASK_USER_QUESTION,
    "ask_user": ASK_USER_QUESTION,
    "requires_hitl": NEEDS_OPERATOR_DECISION,
    "HITL_REQUIRED_TOOLS": TOOL_APPROVAL_REQUIRED,
    "Decision.ASK_USER": TOOL_APPROVAL_REQUIRED,
    "HITL_REQUIRED": OPERATOR_INPUT_REQUIRED,
    "hitl_required": NEEDS_OPERATOR_DECISION,  # default when used as boolean flag
    "hitl_receipt_bound": APPROVAL_SIGNATURE_VALID,
}


@dataclass(frozen=True)
class VocabularyTermDefinition:
    """Structured definition for a canonical vocabulary term."""

    term: str
    concept: OperatorDecisionConcept
    plain_english_meaning: str
    replaces_legacy_terms: tuple[str, ...]
    operational_boundary: str


VOCABULARY_REGISTRY: Dict[str, VocabularyTermDefinition] = {
    ASK_USER_QUESTION: VocabularyTermDefinition(
        term=ASK_USER_QUESTION,
        concept=OperatorDecisionConcept.CHANNEL,
        plain_english_meaning="The only interactive channel for presenting multiple-choice questions to the operator.",
        replaces_legacy_terms=("ask_question", "ask_user"),
        operational_boundary="Interactive UI session only; never executed in unattended or scheduled background runs.",
    ),
    NEEDS_OPERATOR_DECISION: VocabularyTermDefinition(
        term=NEEDS_OPERATOR_DECISION,
        concept=OperatorDecisionConcept.AMBIGUITY_RULE,
        plain_english_meaning="The test deciding that a question must be asked: top-vs-second confidence gap <= 30%.",
        replaces_legacy_terms=("requires_hitl", "hitl_required"),
        operational_boundary="Evaluated mathematically during candidate ranking; triggers ask_user_question if interactive.",
    ),
    TOOL_APPROVAL_REQUIRED: VocabularyTermDefinition(
        term=TOOL_APPROVAL_REQUIRED,
        concept=OperatorDecisionConcept.TOOL_GUARD,
        plain_english_meaning="A binary confirmation before an expensive, destructive, or external tool executes.",
        replaces_legacy_terms=("HITL_REQUIRED_TOOLS", "Decision.ASK_USER"),
        operational_boundary="Permission gate on tool execution; does not formulate option choices or write decision records.",
    ),
    OPERATOR_INPUT_REQUIRED: VocabularyTermDefinition(
        term=OPERATOR_INPUT_REQUIRED,
        concept=OperatorDecisionConcept.RUN_OUTCOME,
        plain_english_meaning="The terminal outcome of an unattended or scheduled run that halted because it needed operator input.",
        replaces_legacy_terms=("HITL_REQUIRED",),
        operational_boundary="Emitted by headless runners when needs_operator_decision is true and no interactive channel is attached.",
    ),
    DECISION_RECORD: VocabularyTermDefinition(
        term=DECISION_RECORD,
        concept=OperatorDecisionConcept.PERSISTENT_RECORD,
        plain_english_meaning="The persistent, verified operator response stored in hitl_decision_ledger.",
        replaces_legacy_terms=("ledger entries", "adjudications"),
        operational_boundary="Written exclusively via decision_record_writer with file locking and Merkle hash verification.",
    ),
    APPROVAL_SIGNATURE_VALID: VocabularyTermDefinition(
        term=APPROVAL_SIGNATURE_VALID,
        concept=OperatorDecisionConcept.APPROVAL_PROOF,
        plain_english_meaning="Cryptographic Ed25519 signature proving an approval or answer originated from an authenticated human.",
        replaces_legacy_terms=("hitl_receipt_bound",),
        operational_boundary="Computed strictly by verifiers from cryptographic evidence; caller assertions discarded.",
    ),
}


# ==============================================================================
# VALIDATION & HELPER FUNCTIONS
# ==============================================================================


def is_canonical_term(term: str) -> bool:
    """Return True if term is one of the six canonical vocabulary terms."""
    return term in CANONICAL_VOCABULARY_TERMS


def is_deprecated_term(term: str) -> bool:
    """Return True if term is a known legacy overloaded term."""
    return term in DEPRECATED_TERMS


def disambiguate_term(term: str) -> Optional[str]:
    """Map a legacy term to its canonical replacement. Returns None if unknown."""
    if term in CANONICAL_VOCABULARY_TERMS:
        return term
    return DISAMBIGUATION_MAP.get(term)


def validate_decision_record_boundary(is_tool_approval: bool, has_option_selection: bool) -> None:
    """Enforce separation of concerns between tool approval and decision records.

    A tool approval is a binary execution permission and MUST NEVER be recorded
    as an option decision record. Similarly, an option decision must never bypass
    the decision record writer.
    """
    if is_tool_approval and has_option_selection:
        raise ValueError(
            "Separation of concerns violation: Tool approvals (TOOL_APPROVAL_REQUIRED) "
            "must never be recorded as multiple-choice decision records (DECISION_RECORD)."
        )
