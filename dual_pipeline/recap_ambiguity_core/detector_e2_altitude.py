"""Domain E2 Detector: Business Catalyst, Economic Stakes & Executive Altitude vs Technical Grounding."""
from __future__ import annotations

from typing import Any, Dict, List

from .models import ExecutiveAmbiguityDomain, ExecutiveAmbiguityRecord, ExecutiveCandidateOption


class DetectorE2Altitude:
    """Detects executive value framing deficiencies or unclear macro business catalysts."""

    CATALYST_KEYWORDS = [
        "throughput", "portfolio", "headcount", "capital", "write-off", "cost", "efficiency",
        "margin", "timeline", "revenue", "bottleneck", "resequence", "developer productivity"
    ]

    def detect(self, metadata: Dict[str, Any], objective_text: str = "") -> List[ExecutiveAmbiguityRecord]:
        ambiguities: List[ExecutiveAmbiguityRecord] = []
        meeting_name = metadata.get("meeting_name", "Meeting")
        objective = objective_text or metadata.get("meeting_objective", "")

        has_catalyst = any(kw in objective.lower() for kw in self.CATALYST_KEYWORDS)

        if objective and not has_catalyst:
            opt_bounded = ExecutiveCandidateOption(
                key="catalyst_operational_throughput",
                label="Frame around Operational Throughput & Resourcing Efficiency",
                description="Frame meeting objective around scaling portfolio throughput against flat headcount constraints",
                confidence=0.52,
                rationale="Standard delivery partner value framing for enterprise digital transformation initiatives",
            )
            opt_risk = ExecutiveCandidateOption(
                key="catalyst_capital_risk",
                label="Frame around Schedule Delay & Capital Write-Off Exposure",
                description="Frame meeting objective around schedule critical path and delivery risk mitigation",
                confidence=0.48,
                rationale="Standard executive governance framing for transmission and engineering capital projects",
            )
            rec = ExecutiveAmbiguityRecord(
                ambiguity_id="amb-e2-altitude-catalyst",
                domain=ExecutiveAmbiguityDomain.E2_VALUE_ALTITUDE,
                title=f"Executive Altitude Framing for: {meeting_name}",
                context_snippet=f"Objective lacks explicit economic or operational catalyst: '{objective[:120]}...'",
                candidates=[opt_bounded, opt_risk],
            )
            if rec.is_gate_eligible():
                ambiguities.append(rec)

        return ambiguities
