"""Domain E3 Detector: Action Item Governance (Actionable imperative verbs, single named DRIs, strict MM/DD/YYYY deadlines)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .models import ExecutiveAmbiguityDomain, ExecutiveAmbiguityRecord, ExecutiveCandidateOption


class DetectorE3Actions:
    """Detects passive action items, unassigned DRIs, or missing MM/DD/YYYY planning deadlines."""

    PASSIVE_VERBS = re.compile(
        r"^\s*(Acknowledge|Note|Consider|Be aware|Discuss|Explore|Observe|Reflect|Recognize|Understand|Appreciate|Keep in mind|TBC|Review)",
        re.IGNORECASE,
    )
    MM_DD_YYYY_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")

    def _check_passive_verb(self, idx: int, action_desc: str) -> Optional[ExecutiveAmbiguityRecord]:
        if not self.PASSIVE_VERBS.match(action_desc):
            return None
        opt_active = ExecutiveCandidateOption(
            key="action_imperative_finalize",
            label=f"Convert to Imperative Deliverable: 'Finalize and submit {action_desc}'",
            description="Replace passive verb with decisive deliverable completion verb",
            confidence=0.55,
            rationale="Enforces single-action accountability with verifiable artifact outcome",
        )
        opt_audit = ExecutiveCandidateOption(
            key="action_imperative_audit",
            label=f"Convert to Verification Gate: 'Audit and validate {action_desc}'",
            description="Replace passive verb with concrete validation checkpoint",
            confidence=0.45,
            rationale="Establishes formal engineering governance checkpoint",
        )
        rec = ExecutiveAmbiguityRecord(
            ambiguity_id=f"amb-e3-passive-{idx}",
            domain=ExecutiveAmbiguityDomain.E3_ACTION_DRI_DEADLINE,
            title=f"Passive Action Item Verb: '{action_desc[:50]}...'",
            context_snippet=f"Action item starts with passive verb: '{action_desc}'",
            candidates=[opt_active, opt_audit],
        )
        return rec if rec.is_gate_eligible() else None

    def _check_unassigned_owner(self, idx: int, action_desc: str, owner: str) -> Optional[ExecutiveAmbiguityRecord]:
        if owner and owner.lower() not in ("tbc", "unknown", "not assigned", "team", "all", "unassigned"):
            return None
        opt_partner_advisory_dri = ExecutiveCandidateOption(
            key="dri_partner_advisory_lead",
            label="Assign to PartnerAdvisory Delivery Lead",
            description="Single accountability assigned to consulting delivery lead",
            confidence=0.52,
            rationale="Engagement delivery model ownership",
        )
        opt_client_dri = ExecutiveCandidateOption(
            key="dri_client_lead",
            label="Assign to Client Engineering Lead",
            description="Single accountability assigned to client internal lead",
            confidence=0.48,
            rationale="Client system governance ownership",
        )
        rec = ExecutiveAmbiguityRecord(
            ambiguity_id=f"amb-e3-owner-{idx}",
            domain=ExecutiveAmbiguityDomain.E3_ACTION_DRI_DEADLINE,
            title=f"Unassigned Action Item Owner for: '{action_desc[:50]}...'",
            context_snippet=f"Action item owner is unassigned or group: '{owner}'",
            candidates=[opt_partner_advisory_dri, opt_client_dri],
        )
        return rec if rec.is_gate_eligible() else None

    def _check_invalid_due_date(self, idx: int, action_desc: str, due_date: str) -> Optional[ExecutiveAmbiguityRecord]:
        if due_date and self.MM_DD_YYYY_PATTERN.match(due_date.strip()):
            return None
        opt_two_week = ExecutiveCandidateOption(
            key="date_two_week_horizon",
            label="Assign Sprint Horizon (2-week follow-up)",
            description="Assign immediate next sprint milestone date in MM/DD/YYYY format",
            confidence=0.53,
            rationale="Aligned with standard 2-week sprint planning cadence",
        )
        opt_month_end = ExecutiveCandidateOption(
            key="date_month_end_horizon",
            label="Assign Month-End Governance Gate",
            description="Assign calendar month-end steering gate in MM/DD/YYYY format",
            confidence=0.47,
            rationale="Aligned with executive monthly steering cadence",
        )
        rec = ExecutiveAmbiguityRecord(
            ambiguity_id=f"amb-e3-date-{idx}",
            domain=ExecutiveAmbiguityDomain.E3_ACTION_DRI_DEADLINE,
            title=f"Missing or Non-Compliant MM/DD/YYYY Due Date for: '{action_desc[:50]}...'",
            context_snippet=f"Due date '{due_date}' is missing or does not match MM/DD/YYYY format.",
            candidates=[opt_two_week, opt_month_end],
        )
        return rec if rec.is_gate_eligible() else None

    def detect_from_items(self, action_items: List[Dict[str, Any]]) -> List[ExecutiveAmbiguityRecord]:
        ambiguities: List[ExecutiveAmbiguityRecord] = []

        for idx, item in enumerate(action_items):
            action_desc = item.get("action", "") or item.get("description", "")
            owner = item.get("owner", "") or item.get("dri", "")
            due_date = item.get("due_date", "") or item.get("date", "")

            rec_passive = self._check_passive_verb(idx, action_desc)
            if rec_passive is not None:
                ambiguities.append(rec_passive)

            rec_owner = self._check_unassigned_owner(idx, action_desc, owner)
            if rec_owner is not None:
                ambiguities.append(rec_owner)

            rec_date = self._check_invalid_due_date(idx, action_desc, due_date)
            if rec_date is not None:
                ambiguities.append(rec_date)

        return ambiguities

