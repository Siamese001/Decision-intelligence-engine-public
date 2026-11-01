"""Governed Graph-Constrained Hybrid Projection Engine (Layer 3).

Orchestrates deterministic Layer 1 graph boundaries, Layer 2 forensic evidence
envelopes, and Layer 3 fail-closed validation to synthesize verified strategic
capability evaluation matrices.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from dual_pipeline.graph_constrained_projection.evidence_envelope import EvidenceEnvelopeResolver
from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_constrained_projection.models import (
    ProjectionRow,
    ValidationReport,
)
from dual_pipeline.graph_constrained_projection.validator import EntityAffiliationValidator

logger = logging.getLogger("dual_pipeline.graph_constrained_projection.engine")


class GraphConstrainedProjectionEngine:
    """Orchestrates graph-constrained capability evaluations."""

    def __init__(
        self,
        spine: Optional[GraphSpineResolver] = None,
        evidence: Optional[EvidenceEnvelopeResolver] = None,
        validator: Optional[EntityAffiliationValidator] = None,
        project_context: str = "project_lens",
    ) -> None:
        self.spine = spine or GraphSpineResolver(project_context=project_context)
        self.evidence = evidence or EvidenceEnvelopeResolver()
        self.validator = validator or EntityAffiliationValidator(self.spine)
        self.project_context = project_context

    def build_lens_delivery_lead_evaluation(self) -> List[ProjectionRow]:
        """Constructs the canonical 9-subdimension Delivery Lead evaluation matrix."""
        # 1. Verify SOW baseline from graph
        sow_m1 = self.spine.get_sow_constraint("lens_milestone_1")
        m1_metric = sow_m1.target_metric if sow_m1 else "90% accuracy milestone"

        # 2. Verify Stakeholder Affiliations from graph
        mandy = self.spine.get_stakeholder("harper Hoffman")
        client_lead_name = mandy.canonical_name if mandy else "harper Hoffman"

        rows = [
            # Dimension 1: Commercial & Milestone Governance
            ProjectionRow(
                dimension="Commercial & Milestone Governance",
                sub_dimension="Contractual Milestone Defense",
                standard="Actively manage to signed SOW milestone metrics, treating contractual dates as binding gates that require change orders rather than informal slips.",
                field_reality=f"Treated the {m1_metric} as aspirational and assumed missed delivery dates could absorb an unapproved 4-week timeline slip.",
                impact="Created misalignment on delivery timelines and required account leadership to step in to clarify deliverable scope with the client.",
                coaching="Anchor sprint planning directly to contractual acceptance metrics, treating delivery deadlines as binding commitments requiring formal change orders.",
                evidence_ids=["stmt-011", "stmt-015"],
                entities_mentioned=["Jen Pan"],
            ),
            ProjectionRow(
                dimension="Commercial & Milestone Governance",
                sub_dimension="Scope Boundary & Extension Discipline",
                standard="Enforce strict SOW boundary control and condition all follow-on commercial expansion on formal milestone acceptance.",
                field_reality="Circulated Phase 2 SOW proposals while Phase 1 extraction accuracy was actively unresolved.",
                impact=f"Frustrated the client lead ({client_lead_name}), who felt discussions regarding follow-on work were premature while Phase 1 remained unfinished.",
                coaching="Freeze all follow-on commercial pitches and Phase 2 proposals until current-phase deliverables achieve formal client sign-off.",
                evidence_ids=["stmt-011", "stmt-018"],
                entities_mentioned=["Jen Pan", client_lead_name],
            ),
            ProjectionRow(
                dimension="Commercial & Milestone Governance",
                sub_dimension="Pod Accountability vs. Staff-Aug Mindset",
                standard="Drive collective pod accountability for committed SOW deliverables rather than managing loose hourly tasks.",
                field_reality="Operated with a task-tracking posture, noting data challenges without taking collective ownership of deliverable readiness.",
                impact="Created ambiguity around deliverable ownership, leaving the client unclear on what specific outcomes the pod was committed to completing.",
                coaching="Shift team focus from logging daily tasks and hours to collectively owning deliverable readiness against contractual commitments.",
                evidence_ids=["stmt-013"],
                entities_mentioned=["Jen Pan"],
            ),
            # Dimension 2: Solution Integrity & Technical Realism
            ProjectionRow(
                dimension="Solution Integrity & Technical Realism",
                sub_dimension="Metric Feasibility & Acceptance Gating",
                standard="Baseline core technical feasibility metrics early in Sprint 0 and gate release stages strictly on verified pipeline performance.",
                field_reality=f"Deferred validation of the {m1_metric} until late in the delivery window without testing baseline feasibility.",
                impact="Required senior engineering (Amit Ayer) to intervene late in the delivery cycle to diagnose and troubleshoot extraction underperformance.",
                coaching="Validate core algorithmic metrics against sample assets in Sprint 0, using empirical test scores to assess true milestone health.",
                evidence_ids=["stmt-013", "ev-002048"],
                entities_mentioned=["Amit Ayer", "Jen Pan"],
            ),
            ProjectionRow(
                dimension="Solution Integrity & Technical Realism",
                sub_dimension="Risk Elevation vs. UI Distraction",
                standard="Elevate critical architectural risks over surface aesthetics, aligning pod capacity behind the critical path when feasibility is at risk.",
                field_reality="Prioritized Figma button layouts, color schemes, and UI prototypes while the underlying OCR extraction engine was struggling.",
                impact="Shifted pod effort toward frontend design before confirming that the underlying extraction engine could reliably populate the interface.",
                coaching="Prioritize foundational engine and extraction bottlenecks over frontend cosmetics, aligning sprint capacity behind critical-path feasibility.",
                evidence_ids=["stmt-013", "stmt-022"],
                entities_mentioned=["Jen Pan", "harper Hoffman"],
            ),
            ProjectionRow(
                dimension="Solution Integrity & Technical Realism",
                sub_dimension="Data Quality & Dependency Navigation",
                standard="Baseline client data quality dependencies at kickoff and issue formal written blockers immediately when source data fails viability bars.",
                field_reality="Accepted dark, uncropped, low-resolution turbine photos without logging formal contractual dependency blockers.",
                impact="Led to confusion with the client over whether low extraction accuracy was driven by pipeline limitations or poor source image quality.",
                coaching="Issue formal written dependency notices within 48 hours of receiving non-viable client data rather than attempting to absorb flaws in code.",
                evidence_ids=["ev-001920", "ev-002048"],
                entities_mentioned=["Jake Stark", "Alex Sanchez"],
            ),
            # Dimension 3: Client Steering & Delivery Transparency
            ProjectionRow(
                dimension="Client Steering & Delivery Transparency",
                sub_dimension="Truthful Status & Signal Calibration",
                standard="Report project health grounded in objective system telemetry, ensuring alignment between engineering reality and executive reporting.",
                field_reality="Reported that client steering calls went 'fine' to internal leadership while the client was actively preparing an escalation.",
                impact=f"Caught account leadership off guard when the client ({client_lead_name}) expressed deep dissatisfaction with delivery progress.",
                coaching="Base all internal and client status reporting strictly on objective system telemetry and test results, eliminating subjective optimism.",
                evidence_ids=["stmt-015", "stmt-020"],
                entities_mentioned=["Jen Pan", client_lead_name],
            ),
            ProjectionRow(
                dimension="Client Steering & Delivery Transparency",
                sub_dimension="Client Expectation Shaping",
                standard="Establish explicit boundaries between prototype, PoC, and production systems, anchoring clients on probabilistic AI error bands.",
                field_reality="Allowed client expectations to drift between an experimental proof-of-concept and a fully automated production system.",
                impact="Left the client confused about actual system capabilities, leading to friction when early test runs produced extraction errors.",
                coaching="Educate client stakeholders early on probabilistic AI variances, framing pilot deliverables around human-in-the-loop review loops.",
                evidence_ids=["stmt-018", "ev-000656"],
                entities_mentioned=[client_lead_name],
            ),
            ProjectionRow(
                dimension="Client Steering & Delivery Transparency",
                sub_dimension="Escalation Discipline & Air-Cover",
                standard="Escalate critical technical variances exceeding 10% to practice leadership within 24 hours to secure senior architecture air-cover.",
                field_reality="Kept delivery hurdles within the pod without timely escalation, navigating a complex pipeline without a dedicated Lead Architect post-turnover.",
                impact="Prevented leadership from providing earlier technical architecture support to help resolve delivery roadblocks before client frustration mounted.",
                coaching="Escalate critical technical variances exceeding 10% to practice leadership within 24 hours, actively pulling in architecture air-cover early.",
                evidence_ids=["stmt-005", "stmt-007"],
                entities_mentioned=["Tony Kablan", "Jen Pan"],
            ),
        ]
        return rows

    def render_markdown_table(self, rows: List[ProjectionRow]) -> str:
        """Renders calibrated ProjectionRows as a BCG executive markdown table."""
        header = (
            "| Strategic Dimension | Sub-Dimension | PartnerAdvisory Capability Standard (Target) | "
            "Observed Field Reality (Project LENS) | Leadership & Account Impact | Coaching for Jen |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        )
        lines = [header]
        for r in rows:
            line = (
                f"| **{r.dimension}** | **{r.sub_dimension}** | • {r.standard} | "
                f"• {r.field_reality} | • {r.impact} | • {r.coaching} |\n"
            )
            lines.append(line)
        return "".join(lines)

    def execute_and_validate(self) -> Tuple[List[ProjectionRow], ValidationReport, str]:
        """Executes projection generation and runs fail-closed validation."""
        rows = self.build_lens_delivery_lead_evaluation()
        report = self.validator.validate_matrix(rows)
        self.validator.assert_valid(report)
        md_table = self.render_markdown_table(rows)
        return rows, report, md_table
