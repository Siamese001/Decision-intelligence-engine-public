"""dual_pipeline.grounding.eligibility

Layer 2 assertion eligibility auditor and anti-hallucination governance.
Enforces:
1. Rejection of candidate, retracted, superseded, or proposed assertions for release.
2. Complete subject/predicate/object matching (entity existence alone cannot certify assertions).
3. Namespace and audience accessibility gating (CLIENT_SAFE vs quarantined/internal).
4. Detection of active contradictions or competing assertions.
5. Typed source locator validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .contracts import ClaimRecord
from .snapshot import ReadOnlyGraphSnapshot


class EligibilityDecision:
    ELIGIBLE = "ELIGIBLE"
    REJECTED_STATUS = "REJECTED_STATUS"
    REJECTED_AUDIENCE = "REJECTED_AUDIENCE"
    REJECTED_CONTRADICTION = "REJECTED_CONTRADICTION"
    REJECTED_STALE = "REJECTED_STALE"
    REJECTED_UNSUPPORTED = "REJECTED_UNSUPPORTED"
    REJECTED_ENTITY_ONLY = "REJECTED_ENTITY_ONLY"
    REJECTED_LOCATOR = "REJECTED_LOCATOR"


@dataclass
class EligibilityResult:
    """Detailed audit verdict for a single claim against graph assertions."""
    claim_id: str
    is_eligible: bool
    decision: str
    matched_assertion_ids: List[str] = field(default_factory=list)
    contradicting_assertion_ids: List[str] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "is_eligible": self.is_eligible,
            "decision": self.decision,
            "matched_assertion_ids": list(self.matched_assertion_ids),
            "contradicting_assertion_ids": list(self.contradicting_assertion_ids),
            "rejection_reasons": list(self.rejection_reasons),
        }


class AssertionEligibilityAuditor:
    """Audits claims against SQLite snapshot assertions under strict fail-closed governance."""

    def __init__(self, allow_candidate_lane: bool = False) -> None:
        self.allow_candidate_lane = allow_candidate_lane

    def _check_locators(self, claim: ClaimRecord) -> Optional[EligibilityResult]:
        """Validate typed source locators presence and completeness."""
        if not claim.source_locators:
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=EligibilityDecision.REJECTED_LOCATOR,
                rejection_reasons=["Claim lacks mandatory source locators."],
            )
        for loc in claim.source_locators:
            if not loc.source_uri or not loc.locator_value:
                return EligibilityResult(
                    claim_id=claim.claim_id,
                    is_eligible=False,
                    decision=EligibilityDecision.REJECTED_LOCATOR,
                    rejection_reasons=[f"Incomplete source locator: {loc.locator_type}"],
                )
        return None

    def _check_status_and_lane(self, claim: ClaimRecord) -> Optional[EligibilityResult]:
        """Validate claim evidence lane and epistemic status."""
        if claim.evidence_lane != "approved" and not self.allow_candidate_lane:
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=EligibilityDecision.REJECTED_STATUS,
                rejection_reasons=[f"Evidence lane '{claim.evidence_lane}' is not qualified for release certification."],
            )
        if claim.epistemic_status in ("retracted", "superseded", "disputed"):
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=EligibilityDecision.REJECTED_STATUS,
                rejection_reasons=[f"Claim epistemic status is '{claim.epistemic_status}'."],
            )
        return None

    def _check_entity_only(self, claim: ClaimRecord, snapshot: ReadOnlyGraphSnapshot) -> EligibilityResult:
        """Differentiate entity existence from complete relationship presence."""
        conn = snapshot.connect()
        cur = conn.cursor()
        schema = snapshot._get_schema_info(conn)
        node_tbl = schema.get("node_tbl") or "canonical_graph_nodes"
        node_cols = schema.get("node_cols") or set()
        name_col = "canonical_name" if "canonical_name" in node_cols else ("display_name" if "display_name" in node_cols else "name")
        try:
            if node_tbl == "node":
                cur.execute("SELECT node_id FROM node WHERE LOWER(canonical_name) = LOWER(?)", (claim.subject,))
            else:
                cur.execute("SELECT node_id FROM canonical_graph_nodes WHERE LOWER(name) = LOWER(?)", (claim.subject,))
            subj_node = cur.fetchone()
        except Exception:
            subj_node = None

        if subj_node:
            reason = (
                f"Entity '{claim.subject}' exists, but asserted relationship '{claim.predicate} -> {claim.object}' "
                "is completely missing from graph assertions."
            )
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=EligibilityDecision.REJECTED_ENTITY_ONLY,
                rejection_reasons=[reason],
            )
        return EligibilityResult(
            claim_id=claim.claim_id,
            is_eligible=False,
            decision=EligibilityDecision.REJECTED_UNSUPPORTED,
            rejection_reasons=[f"No graph records found for subject '{claim.subject}' and predicate '{claim.predicate}'."],
        )

    def _check_assertion_lifecycle(self, assn: Dict[str, Any], assn_id: str) -> Optional[str]:
        status = str(assn.get("status") or assn.get("canonical_status") or "CONFIRMED").upper()
        disallowed_statuses = (
            ("SUPERSEDED", "RETRACTED", "REJECTED")
            if self.allow_candidate_lane
            else ("SUPERSEDED", "RETRACTED", "PROPOSED", "CANDIDATE", "HISTORICAL_CANDIDATE", "REJECTED")
        )
        if status in disallowed_statuses:
            return f"Assertion {assn_id} has unqualified status '{status}'."
        val_state = str(assn.get("validation_state") or "").lower()
        disallowed_val_states = (
            ("superseded", "retracted")
            if self.allow_candidate_lane
            else ("superseded", "retracted", "proposed")
        )
        if val_state in disallowed_val_states:
            return f"Assertion {assn_id} has unqualified validation_state '{val_state}'."
        cur_state = str(assn.get("currentness_state") or "").lower()
        allowed_cur = ("current", "unknown") if self.allow_candidate_lane else ("current",)
        if cur_state and cur_state not in allowed_cur:
            return f"Assertion {assn_id} has non-current state '{cur_state}'."
        return None

    def _check_assertion_canonical(self, assn: Dict[str, Any], assn_id: str) -> Optional[str]:
        if not self.allow_candidate_lane:
            can_status = str(assn.get("canonical_status") or "").lower()
            if can_status and can_status != "canonical":
                return f"Assertion {assn_id} has non-canonical status '{can_status}'."
            can_elig = assn.get("canonical_eligible")
            if can_elig is not None and not bool(can_elig):
                return f"Assertion {assn_id} has canonical_eligible=0."
        if assn.get("retrieval_eligible") is not None and not bool(assn.get("retrieval_eligible")):
            return f"Assertion {assn_id} has retrieval_eligible=0."
        conf_raw = assn.get("confidence_score")
        if conf_raw is not None and float(conf_raw) < 0.65:
            return f"Assertion {assn_id} has insufficient confidence_score={conf_raw} (< 0.65)."
        return None

    def _check_assertion_expiration(self, assn: Dict[str, Any], assn_id: str, now_dt: datetime) -> Optional[str]:
        stale_after = assn.get("stale_after")
        if stale_after:
            try:
                stale_dt = datetime.fromisoformat(stale_after.replace("Z", "+00:00"))
                if now_dt > stale_dt:
                    return f"Assertion {assn_id} expired at {stale_after}."
            except Exception:
                pass
        return None

    def _check_assertion_audience(self, assn: Dict[str, Any], assn_id: str, audience: str) -> Optional[str]:
        namespace = assn.get("namespace", "CLIENT_SAFE")
        aud_low = audience.lower()
        if namespace != "CLIENT_SAFE" and not any(
            tok in aud_low for tok in ("internal", "nee-engineering", "partner_advisory")
        ):
            return f"Assertion {assn_id} namespace '{namespace}' inaccessible to audience '{audience}'."
        return None

    def _check_assertion_validity(
        self,
        assn: Dict[str, Any],
        audience: str,
        now_dt: datetime,
    ) -> Optional[str]:
        assn_id = assn["assertion_id"]
        return (
            self._check_assertion_lifecycle(assn, assn_id)
            or self._check_assertion_canonical(assn, assn_id)
            or self._check_assertion_expiration(assn, assn_id, now_dt)
            or self._check_assertion_audience(assn, assn_id, audience)
        )

    def _filter_valid_assertions(
        self,
        assertions: List[Dict[str, Any]],
        audience: str,
        now_dt: datetime,
    ) -> Tuple[List[str], List[str]]:
        """Filter candidate assertions against status, expiration, and audience boundaries."""
        valid_ids: List[str] = []
        reasons: List[str] = []
        for assn in assertions:
            reason = self._check_assertion_validity(assn, audience, now_dt)
            if reason:
                reasons.append(reason)
            else:
                valid_ids.append(assn["assertion_id"])
        return valid_ids, reasons

    def audit_claim(
        self,
        claim: ClaimRecord,
        snapshot: ReadOnlyGraphSnapshot,
        audience: str = "internal_consulting",
        as_of_time: Optional[datetime] = None,
    ) -> EligibilityResult:
        now_dt = as_of_time or datetime.now(timezone.utc)

        # 1. Validate locators
        loc_err = self._check_locators(claim)
        if loc_err is not None:
            return loc_err

        # 2. Validate lane and status
        status_err = self._check_status_and_lane(claim)
        if status_err is not None:
            return status_err

        # 3. Find matching assertions
        matching = snapshot.find_assertions_for_claim(
            claim.subject, claim.predicate, claim.object, include_candidates=self.allow_candidate_lane
        )
        if not matching:
            return self._check_entity_only(claim, snapshot)

        # 4. Filter assertions
        valid_ids, reasons = self._filter_valid_assertions(matching, audience, now_dt)
        if not valid_ids:
            if any("inaccessible to audience" in r for r in reasons):
                dec = EligibilityDecision.REJECTED_AUDIENCE
            elif reasons:
                dec = EligibilityDecision.REJECTED_STATUS
            else:
                dec = EligibilityDecision.REJECTED_UNSUPPORTED
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=dec,
                rejection_reasons=reasons,
            )

        # 5. Check contradictions
        if claim.contradicting_assertion_ids:
            return EligibilityResult(
                claim_id=claim.claim_id,
                is_eligible=False,
                decision=EligibilityDecision.REJECTED_CONTRADICTION,
                matched_assertion_ids=valid_ids,
                contradicting_assertion_ids=list(claim.contradicting_assertion_ids),
                rejection_reasons=[
                    f"Claim references unresolved contradicting assertions: {claim.contradicting_assertion_ids}."
                ],
            )

        return EligibilityResult(
            claim_id=claim.claim_id,
            is_eligible=True,
            decision=EligibilityDecision.ELIGIBLE,
            matched_assertion_ids=valid_ids,
            contradicting_assertion_ids=[],
            rejection_reasons=[],
        )
