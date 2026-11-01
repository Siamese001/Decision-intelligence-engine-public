"""Two-Track Governance Classifier for Invalidation Candidates.

Distinguishes between:
- Track A: Discretionary Strategic Focus & Delivery Milestones (can be auto-superseded)
- Track B: Objective Compliance, Commercial & Security Blockers (fail-closed, cannot be auto-superseded)
Enforces the 15% ambiguity margin rule (Delta = c_top - c_second >= 0.15).
"""
from __future__ import annotations

from typing import Optional

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

from .models import TwoTrackClassification

# Keywords indicating hard compliance, security, or commercial liability
_TRACK_B_KEYWORDS = frozenset({
    "vpc",
    "public_api",
    "public api",
    "incident",
    "cybersecurity",
    "security",
    "legal",
    "compliance",
    "commercial authority",
    "sow authority",
    "pricing workbook",
    "etrm_pricing",
    "etrm pricing",
    "pricing",
    "investor claims",
    "investor-deck",
    "investor deck",
    "external shareability",
    "external claims",
    "contract boundary",
    "signed sow",
    "po / co",
})

# Known explicit Track B validation IDs
_TRACK_B_EXPLICIT_IDS = frozenset({
    "val_gordon_public_api_vpc",
    "v26_action_001_val_gordon_public_api_vpc",
    "validation_v23_google_external_claims_p0",
    "v26_action_002_validation_v23_google_external_claims_p0",
    "validation_adaptive_ai_pdlc_external_shareability_p0",
    "v26_action_003_validation_adaptive_ai_pdlc_external_shareability_p0",
    "validation_v23_etrm_pricing_commercials_p0",
    "v26_action_004_validation_v23_etrm_pricing_commercials_p0",
    "validation_v27_gemini_sow_authority_p0",
    "action_validation_v27_gemini_sow_authority_p0",
    "validation_v27_gemini_scope_boundary_p0",
    "action_validation_v27_gemini_scope_boundary_p0",
    "val_v39_mercury_formal_classification",
    "action_val_v39_mercury_formal_classification",
})

# Keywords indicating strategic focus, methodology, or engineering staffing
_TRACK_A_KEYWORDS = frozenset({
    "sdlc",
    "ai sdlc",
    "evals",
    "agentic control",
    "106-swe",
    "106 swe",
    "swe decomposition",
    "pod model",
    "pod",
    "forum cadence",
    "partner forum",
    "product-line inventory",
    "operating model",
    "tech lead",
    "screening standard",
    "candidate capability",
    "roster alignment",
})


class TwoTrackClassifier:
    """Classifies candidate items into Track A (Strategic) vs Track B (Compliance)."""

    def __init__(self, ambiguity_threshold: Optional[float] = None):
        self.ambiguity_threshold = (
            ambiguity_threshold
            if ambiguity_threshold is not None
            else get_hitl_ambiguity_margin_threshold()
        )

    def classify_item(
        self,
        item_id: str,
        item_type: str,
        title_or_claim: str,
        lane_or_predicate: Optional[str] = None,
        priority: Optional[str] = None,
        context: Optional[str] = None,
    ) -> TwoTrackClassification:
        """Evaluate an item and return its calibrated classification and confidence."""
        # 1. Check explicit registry
        if item_id in _TRACK_B_EXPLICIT_IDS:
            return TwoTrackClassification(
                item_id=item_id,
                item_type=item_type,
                track="TRACK_B_COMPLIANCE",
                confidence_score=0.98,
                second_confidence_score=0.02,
                margin_delta=0.96,
                hitl_required=False,
                rationale=f"Explicitly registered Track B compliance blocker ({item_id}).",
            )

        # 2. Text analysis across title, lane, and context
        combined_text = " ".join(
            str(x or "").lower() for x in (item_id, title_or_claim, lane_or_predicate, context)
        )

        b_matches = [kw for kw in _TRACK_B_KEYWORDS if kw in combined_text]
        a_matches = [kw for kw in _TRACK_A_KEYWORDS if kw in combined_text]

        # Prioritize Track B if it is P0 or contains security/commercial keywords
        is_p0 = (priority or "").upper() == "P0"

        score_b = 0.50
        if is_p0:
            score_b += 0.20
        if b_matches:
            score_b += 0.25 * min(len(b_matches), 2)
        if "security" in combined_text or "vpc" in combined_text or "pricing" in combined_text:
            score_b += 0.15

        score_a = 0.50
        if a_matches:
            score_a += 0.25 * min(len(a_matches), 2)
        if not is_p0:
            score_a += 0.10

        # Normalize probabilities
        total = score_a + score_b
        prob_b = score_b / total
        prob_a = score_a / total

        if prob_b > prob_a:
            top_track = "TRACK_B_COMPLIANCE"
            c_top = prob_b
            c_second = prob_a
        else:
            top_track = "TRACK_A_STRATEGIC"
            c_top = prob_a
            c_second = prob_b

        margin = round(c_top - c_second, 4)
        hitl_required = margin < self.ambiguity_threshold

        rationale = (
            f"Classified as {top_track} (prob={c_top:.2f}, margin={margin:.2f}). "
            f"Matches Track B: {b_matches}, Matches Track A: {a_matches}."
        )

        return TwoTrackClassification(
            item_id=item_id,
            item_type=item_type,
            track=top_track,
            confidence_score=c_top,
            second_confidence_score=c_second,
            margin_delta=margin,
            hitl_required=hitl_required,
            rationale=rationale,
        )
