#!/usr/bin/env python3
"""
Pipeline B: Multi-Dimensional QA & Stealth Judges (Law 5 Enforcement)
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Metal)

Enforces Law 5: Multi-Dimensional Soft-Quarantine & Incremental Entity Resolution.
The QA Micro-Judge must NOT delete tactical or low-leverage nodes.
It acts as a non-destructive metadata tagger applying:
  - `strategic_value` (0.0 - 10.0)
  - `tacticality` (0.0 - 10.0)
  - `evidence_quality` (0.0 - 1.0)
  - `confidence` (0.0 - 1.0)
  - `is_quarantined` (bool)
  - `quarantine_reason` (str)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from storage.database import (
    AdvisoryCategory,
    AssertionRecord,
    CanonicalGraphNode,
    EvidenceRecord,
    InternalAdvisoryNode,
)

# Optional OpenTelemetry tracing integration
try:
    from dual_pipeline.telemetry import (
        get_tracer,
        record_eval_result,
        record_quarantine_event,
    )
    tracer = get_tracer("pipeline_b_extraction.judges")
except Exception:
    tracer = None

logger = logging.getLogger("pipeline_b_extraction.judges")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [JUDGE] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass
class JudgeResult:
    is_quarantined: bool
    strategic_value: float
    tacticality: float
    evidence_quality: float
    confidence: float
    quarantine_reason: Optional[str] = None


class QualityJudge:
    """
    Evaluates Canonical Public Graph Nodes and Assertions before commit.
    Enforces Law 5: Non-destructive metadata tagging & Incremental Entity Resolution.
    """

    VALID_ASSERTION_TYPES = {"DECISION", "RISK", "DEPENDENCY", "TECHNICAL", "STRATEGIC", "SUMMARY"}

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalizes entity name for fuzzy identity resolution."""
        cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", name.lower())
        return re.sub(r"\s+", " ", cleaned).strip()

    def resolve_entity(
        self,
        node: CanonicalGraphNode,
        existing_nodes: List[CanonicalGraphNode],
    ) -> CanonicalGraphNode:
        """
        Incremental Entity Resolution: Reconciles newly extracted entity against
        canonical registry before committing. If a match is found, aligns node_id.
        """
        norm_name = self._normalize_name(node.name)
        for existing in existing_nodes:
            if existing.namespace != "CLIENT_SAFE":
                continue
            if existing.node_id == node.node_id:
                continue
            if self._normalize_name(existing.name) == norm_name:
                logger.info(
                    f"[ENTITY-RESOLUTION] Reconciled '{node.name}' ({node.node_id}) "
                    f"-> existing '{existing.name}' ({existing.node_id})"
                )
                node.node_id = existing.node_id
                break
        return node

    @staticmethod
    def _calculate_node_grounding(node: CanonicalGraphNode, evidence: Optional[EvidenceRecord]) -> float:
        evidence_txt = evidence.text.lower() if evidence else ""
        node_name_lower = node.name.lower()
        context_txt = str(node.properties.get("context", "")).lower()

        term_overlap = sum(1 for w in node_name_lower.split() if len(w) > 2 and w in evidence_txt)
        if term_overlap > 0:
            return min(1.0, term_overlap / max(1, len(node_name_lower.split())))
        if context_txt:
            ctx_words = [w for w in context_txt.split() if len(w) > 3]
            overlap_ctx = sum(1 for w in ctx_words if w in evidence_txt)
            return min(1.0, overlap_ctx / max(1, len(ctx_words))) if ctx_words else 1.0
        return node.evidence_quality

    @staticmethod
    def _evaluate_node_strategic_posture(
        node: CanonicalGraphNode, grounding_score: float
    ) -> Tuple[float, float, float]:
        node_name_lower = node.name.lower()
        tactical_keywords = ["schedule", "invite", "calendar", "follow-up", "to-do", "task", "ticket", "email"]
        is_overtly_tactical = (
            node.type in ["OPERATIONAL_TODO", "TODO", "TASK"]
            or any(kw in node_name_lower for kw in tactical_keywords)
        )
        tacticality = 8.5 if is_overtly_tactical else node.tacticality
        strategic_value = 2.0 if is_overtly_tactical else max(node.strategic_value, 6.0)
        confidence = max(0.5, grounding_score * 0.95)
        return tacticality, strategic_value, confidence

    @staticmethod
    def _evaluate_node_quarantine(
        tacticality: float, strategic_value: float, grounding_score: float
    ) -> Tuple[bool, Optional[str]]:
        if tacticality >= 7.0 and strategic_value < 4.0:
            return True, "TACTICAL_SUPPRESSION: High tacticality with low strategic value"
        if grounding_score < 0.2:
            return True, "UNGROUNDED_EVIDENCE: Node claims lack verbatim grounding in evidence"
        return False, None

    def judge_node(
        self,
        node: CanonicalGraphNode,
        evidence: Optional[EvidenceRecord] = None,
    ) -> CanonicalGraphNode:
        """
        Evaluates and mutates node metadata in-place. Never deletes or drops nodes.
        """
        grounding_score = self._calculate_node_grounding(node, evidence)
        tacticality, strategic_value, confidence = self._evaluate_node_strategic_posture(
            node, grounding_score
        )
        is_quarantined, quarantine_reason = self._evaluate_node_quarantine(
            tacticality, strategic_value, grounding_score
        )

        node.strategic_value = round(strategic_value, 2)
        node.tacticality = round(tacticality, 2)
        node.evidence_quality = round(grounding_score, 2)
        node.confidence = round(confidence, 2)
        node.is_quarantined = is_quarantined
        node.quarantine_reason = quarantine_reason

        if tracer:
            with tracer.start_as_current_span("pipeline.b.quality_judge") as span:
                record_eval_result(
                    span,
                    gate_id="quality_judge_canonical",
                    verdict="QUARANTINED" if is_quarantined else "PASSED",
                    score=node.strategic_value,
                    extra_attributes={
                        "node.id": node.node_id,
                        "node.type": node.type,
                        "tacticality": node.tacticality,
                        "is_quarantined": node.is_quarantined,
                    },
                )
                if is_quarantined:
                    record_quarantine_event(
                        span,
                        node_id=node.node_id,
                        reason=quarantine_reason or "Soft quarantined by rule",
                        rubric_scores={
                            "tacticality": node.tacticality,
                            "strategic_value": node.strategic_value,
                        },
                    )

        return node

    def judge_assertion(self, assertion: AssertionRecord) -> AssertionRecord:
        """Validates W2 Semantic Spine first-class assertions."""
        atype = assertion.assertion_type.upper()
        if atype not in self.VALID_ASSERTION_TYPES:
            logger.warning(f"Coercing invalid assertion type {atype} to SUMMARY")
            assertion.assertion_type = "SUMMARY"
        else:
            assertion.assertion_type = atype

        status = assertion.status.upper()
        if status not in {"PROPOSED", "CONFIRMED", "SUPERSEDED", "RETRACTED"}:
            assertion.status = "CONFIRMED"
        else:
            assertion.status = status

        if assertion.observed_at and assertion.stale_after:
            try:
                if assertion.stale_after < assertion.observed_at:
                    logger.warning(
                        f"Temporal inversion detected for assertion {assertion.assertion_id}: "
                        f"stale_after ({assertion.stale_after}) < observed_at ({assertion.observed_at}). "
                        "Setting status to RETRACTED and penalizing confidence to 0.0."
                    )
                    assertion.status = "RETRACTED"
                    assertion.confidence = 0.0
            except Exception:
                pass

        assertion.confidence = round(min(1.0, max(0.0, assertion.confidence)), 2)
        return assertion


QualityMicroJudge = QualityJudge


class StealthJudge:
    """
    Evaluates Internal Advisory Nodes before commit to SHADOW_GRAPH.db.
    Enforces category governance (STAKEHOLDER_DYNAMICS, DECISION_INFLUENCE, ADOPTION_RISK, BLOCKER)
    and validates high-signal strategic value.
    """

    def judge_node(
        self,
        node: InternalAdvisoryNode,
        evidence: Optional[EvidenceRecord] = None,
    ) -> InternalAdvisoryNode:
        """
        Tags internal advisory nodes with stealth sensitivity and soft-quarantine tags.
        """
        valid_cat = isinstance(node.category, AdvisoryCategory) or (
            str(node.category).upper() in AdvisoryCategory.__members__
        )

        is_quarantined = False
        quarantine_reason = None

        if not valid_cat:
            is_quarantined = True
            quarantine_reason = f"INVALID_CATEGORY: {node.category} not in governed advisory taxonomy"
        elif node.strategic_value < 4.0:
            is_quarantined = True
            quarantine_reason = f"LOW_STRATEGIC_VALUE: Advisory observation below threshold ({node.strategic_value}/10)"

        node.is_quarantined = is_quarantined
        node.quarantine_reason = quarantine_reason
        node.confidence = round(max(0.7, node.confidence), 2)
        node.stealth_score = round(max(0.5, node.stealth_score), 2)

        if tracer:
            with tracer.start_as_current_span("pipeline.b.stealth_judge") as span:
                record_eval_result(
                    span,
                    gate_id="stealth_judge_advisory",
                    verdict="QUARANTINED" if is_quarantined else "PASSED",
                    score=node.strategic_value,
                    extra_attributes={
                        "node.id": node.node_id,
                        "category": node.category.value if hasattr(node.category, "value") else str(node.category),
                        "is_quarantined": node.is_quarantined,
                    },
                )

        return node
