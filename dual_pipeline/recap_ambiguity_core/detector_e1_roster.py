"""Domain E1 Detector: Attendee Affiliation, Organizational Grouping, and DRI Identity Resolution."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import ExecutiveAmbiguityDomain, ExecutiveAmbiguityRecord, ExecutiveCandidateOption


class DetectorE1Roster:
    """Detects attendee affiliation ambiguities and missing organization groupings."""

    KNOWN_ORGS = {
        "partner_advisory": "PartnerAdvisory Consulting",
        "apex_global": "Apex Global Enterprises",
        "apex": "Apex Power & Grid",
        "florida power & light": "Apex Power & Grid",
        "nea": "Apex Global Enterprises",
    }

    def _evaluate_attendee(self, idx: int, att: Any) -> Optional[ExecutiveAmbiguityRecord]:
        name = att.get("name") or (att if isinstance(att, str) else f"Attendee_{idx}")
        org = att.get("organization") or att.get("company") if isinstance(att, dict) else None

        if org and org.lower() not in ("unknown", "tbd", "unassigned", ""):
            return None

        email = att.get("email", "") if isinstance(att, dict) else ""
        opt_partner_advisory = ExecutiveCandidateOption(
            key="org_partner_advisory",
            label=f"{name} -> PartnerAdvisory Consulting (Delivery Partner)",
            description="Assign attendee to PartnerAdvisory delivery team",
            confidence=0.55 if "partner_advisory" in email.lower() else 0.45,
            rationale="Inferred from delivery partner context or email domain",
        )
        opt_client = ExecutiveCandidateOption(
            key="org_client",
            label=f"{name} -> Client (Apex Global Enterprises / APEX)",
            description="Assign attendee to Client organization",
            confidence=0.50 if any(k in email.lower() for k in ("apex", "apex_global")) else 0.45,
            rationale="Inferred from client domain or meeting host context",
        )
        rec = ExecutiveAmbiguityRecord(
            ambiguity_id=f"amb-e1-roster-{idx}",
            domain=ExecutiveAmbiguityDomain.E1_AFFILIATION_ROSTER,
            title=f"Ambiguous Organizational Affiliation: {name}",
            context_snippet=f"Attendee '{name}' lacks explicit organization grouping in meeting metadata.",
            candidates=[opt_partner_advisory, opt_client],
        )
        return rec if rec.is_gate_eligible() else None

    def detect(self, metadata: Dict[str, Any]) -> List[ExecutiveAmbiguityRecord]:
        ambiguities: List[ExecutiveAmbiguityRecord] = []
        attendees = metadata.get("attendees", [])
        if not attendees:
            return ambiguities

        for idx, att in enumerate(attendees):
            rec = self._evaluate_attendee(idx, att)
            if rec is not None:
                ambiguities.append(rec)

        return ambiguities
