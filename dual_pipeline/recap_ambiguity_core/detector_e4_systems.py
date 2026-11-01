"""Domain E4 Detector: Architectural Systems Taxonomy & System Interplay Grounding."""
from __future__ import annotations

from typing import List

from .models import ExecutiveAmbiguityDomain, ExecutiveAmbiguityRecord, ExecutiveCandidateOption


class DetectorE4Systems:
    """Detects conflation of enterprise platforms and systems integration ambiguities."""

    PLATFORMS = {
        "unifier": ("Oracle Primavera Unifier", "Capital project and cost governance portal"),
        "lams": ("LAMS", "Land Acquisition and Management System"),
        "p6": ("Oracle Primavera P6", "Enterprise scheduling and critical path engine"),
        "primavera": ("Oracle Primavera", "Scheduling suite"),
        "documentum": ("OpenText Documentum", "Authoritative engineering records repository"),
        "gis": ("ESRI GIS", "Geospatial mapping and transmission corridor telemetry"),
        "power bi": ("Power BI", "Executive reporting dashboard layer"),
    }

    def detect_from_text(self, text: str) -> List[ExecutiveAmbiguityRecord]:
        ambiguities: List[ExecutiveAmbiguityRecord] = []
        lower_text = text.lower()

        # Check for ambiguous system pairing without explicit architectural demarcation
        has_demarcation = any(k in lower_text for k in ("handoff interface defined", "formal system boundary", "demarcated interface"))
        if "unifier" in lower_text and "lams" in lower_text and not has_demarcation:
            opt_unifier_lead = ExecutiveCandidateOption(
                key="sys_unifier_primary",
                label="Demarcate Unifier as Primary Cost Gate, LAMS as Parcel Ingestion",
                description="Clarify that Unifier holds funding authorization while LAMS tracks parcel acquisition",
                confidence=0.54,
                rationale="Standard APEX Capital Projects architecture boundary",
            )
            opt_lams_lead = ExecutiveCandidateOption(
                key="sys_lams_primary",
                label="Demarcate LAMS as Pre-Condition to Unifier Work Package Creation",
                description="Clarify that LAMS boundary clearance triggers Unifier requisition creation",
                confidence=0.46,
                rationale="Transmission ROW acquisition sequence",
            )
            rec = ExecutiveAmbiguityRecord(
                ambiguity_id="amb-e4-unifier-lams-interface",
                domain=ExecutiveAmbiguityDomain.E4_SYSTEMS_GROUNDING,
                title="System Interface Demarcation: Unifier vs. LAMS",
                context_snippet="Both Unifier and LAMS mentioned without explicit handoff interface definition.",
                candidates=[opt_unifier_lead, opt_lams_lead],
            )
            if rec.is_gate_eligible():
                ambiguities.append(rec)

        return ambiguities
