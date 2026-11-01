"""Domain G3 Detector: Epistemic Credibility & Claim Status Verification.

Ensures that statements in Output 03 are correctly categorized according to
epistemic status: DECISION vs PROPOSAL vs RISK vs ASSUMPTION.
Prevents unapproved ideas or speculative proposals from polluting CANONICAL_GRAPH.db
as binding enterprise decisions.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import GraphAmbiguityDomain, GraphAmbiguityRecord, GraphCandidateOption

SPECULATIVE_MARKERS = [
    r"\bwe could\b",
    r"\bwe might\b",
    r"\bconsidering\b",
    r"\bpotential\b",
    r"\bproposal\b",
    r"\bproposed\b",
    r"\bthinking about\b",
    r"\bwhat if\b",
    r"\bopen question\b",
    r"\bidea\b",
]

DECISIVE_MARKERS = [
    r"\bwe agreed\b",
    r"\bdecided\b",
    r"\bdecision\b",
    r"\bapproved\b",
    r"\bcommitted to\b",
    r"\bfinalized\b",
    r"\bmoving forward with\b",
]


class G3EpistemicClaimDetector:
    """Detects epistemic status ambiguity between decisions, proposals, and risks."""

    def detect(
        self,
        f1_text: str,
        f3_candidate: Optional[Dict[str, Any]] = None,
    ) -> List[GraphAmbiguityRecord]:
        ambiguities: List[GraphAmbiguityRecord] = []

        if not f3_candidate:
            return ambiguities

        mutations = f3_candidate.get("graph_mutations", {})
        decision_updates = mutations.get("decision_updates", [])
        proposal_updates = mutations.get("proposal_updates", [])

        # 1. Check decisions for speculative language (disguised proposals)
        for idx, dec in enumerate(decision_updates):
            if not isinstance(dec, dict):
                continue
            dec_id = dec.get("decision_id", f"decision:{idx}")
            statement = dec.get("statement", "") or dec.get("rationale", "")
            lower_stmt = statement.lower()

            has_speculative = any(re.search(marker, lower_stmt) for marker in SPECULATIVE_MARKERS)
            has_decisive = any(re.search(marker, lower_stmt) for marker in DECISIVE_MARKERS)

            if has_speculative and not has_decisive:
                candidates = [
                    GraphCandidateOption(
                        key="PROPOSAL",
                        label="Demote to PROPOSAL (proposal_updates)",
                        description=f"Statement '{statement[:80]}...' expresses speculative intent, not binding alignment.",
                        confidence=0.55,
                        rationale="Contains speculative modal verbs without explicit approval markers.",
                        metadata={"decision_id": dec_id, "target_array": "proposal_updates"},
                    ),
                    GraphCandidateOption(
                        key="DECISION",
                        label="Retain as DECISION (decision_updates)",
                        description=f"Statement '{statement[:80]}...' represents an authoritative commitment.",
                        confidence=0.45,
                        rationale="Speaker authority may confer binding decision status despite informal language.",
                        metadata={"decision_id": dec_id, "target_array": "decision_updates"},
                    ),
                ]
                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G3-EPISTEMIC-DEC-{idx}",
                    domain=GraphAmbiguityDomain.G3_EPISTEMIC_CLAIM_STATUS,
                    title=f"Epistemic Status Ambiguity: Decision vs Proposal for '{dec_id}'",
                    context_snippet=f"Statement: \"{statement}\"",
                    candidates=candidates,
                )
                ambiguities.append(rec)

        # 2. Check proposals for authoritative language (unrecorded decisions)
        for idx, prop in enumerate(proposal_updates):
            if not isinstance(prop, dict):
                continue
            prop_id = prop.get("proposal_id", f"proposal:{idx}")
            statement = prop.get("statement", "") or prop.get("rationale", "")
            lower_stmt = statement.lower()

            has_decisive = any(re.search(marker, lower_stmt) for marker in DECISIVE_MARKERS)
            if has_decisive:
                candidates = [
                    GraphCandidateOption(
                        key="DECISION",
                        label="Promote to DECISION (decision_updates)",
                        description=f"Statement '{statement[:80]}...' contains decisive approval markers.",
                        confidence=0.56,
                        rationale="Source text confirms explicit consensus or leadership sign-off.",
                        metadata={"proposal_id": prop_id, "target_array": "decision_updates"},
                    ),
                    GraphCandidateOption(
                        key="PROPOSAL",
                        label="Retain as PROPOSAL (proposal_updates)",
                        description=f"Statement '{statement[:80]}...' remains subject to further governance review.",
                        confidence=0.44,
                        rationale="Decision may be provisional pending architectural or steering committee review.",
                        metadata={"proposal_id": prop_id, "target_array": "proposal_updates"},
                    ),
                ]
                rec = GraphAmbiguityRecord(
                    ambiguity_id=f"G3-EPISTEMIC-PROP-{idx}",
                    domain=GraphAmbiguityDomain.G3_EPISTEMIC_CLAIM_STATUS,
                    title=f"Epistemic Status Ambiguity: Proposal vs Decision for '{prop_id}'",
                    context_snippet=f"Statement: \"{statement}\"",
                    candidates=candidates,
                )
                ambiguities.append(rec)

        return ambiguities
