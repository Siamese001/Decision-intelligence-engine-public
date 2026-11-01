"""dual_pipeline/attachment_core/value_classifier.py

Lightweight classifier evaluating whether extracted OCR text / visual components
meet the substantive value-added threshold for Output 02 and Output 03 reflection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from .types import AttachmentRecord, ExtractedAttachmentContent


class CollateralValueTier(str, Enum):
    """Classification of visual collateral value for downstream synthesis."""
    HIGH_VALUE = "HIGH_VALUE"          # Technical architecture, data flow, metrics, system tables
    MEDIUM_VALUE = "MEDIUM_VALUE"      # Agenda, organizational chart, meeting notes
    LOW_VALUE = "LOW_VALUE"            # Decorative icons, title slide, blank template
    FAILED_EXTRACTION = "FAILED_EXTRACTION"  # Failed, corrupted, or unreadable extraction


@dataclass(frozen=True)
class CollateralValueAssessment:
    """Assessment of whether visual collateral adds value to meeting minutes and knowledge graph."""
    tier: CollateralValueTier
    is_value_added: bool
    score: float
    reasons: List[str]
    technical_keywords_found: List[str]
    extraction_status: str = "SUCCESS"
    error_reason: Optional[str] = None
    is_failed: bool = False


TECHNICAL_INDICATORS = {
    "architecture", "pipeline", "ingestion", "database", "graph", "api", "service",
    "cluster", "latency", "throughput", "schema", "flow", "cloud", "aws", "gcp",
    "azure", "scada", "telemetry", "model", "llm", "vlm", "ocr", "table", "metric",
    "schedule", "sow", "milestone", "phase", "component", "interface", "gateway",
}

NUMERIC_METRIC_PATTERN = re.compile(r"\b\d+(?:\.\d+)?(?:%|ms|s|m|k|gb|tb|assets|turbines|wo|wos)\b", re.IGNORECASE)


def _score_components_and_connections(content: ExtractedAttachmentContent, reasons: List[str]) -> float:
    score = 0.0
    comp_count = len(content.components) if content.components else 0
    if comp_count >= 3:
        score += 0.4
        reasons.append(f"Contains {comp_count} structured system components.")
    elif comp_count >= 1:
        score += 0.2
        reasons.append(f"Contains {comp_count} identified component.")

    conn_count = len(content.connections) if content.connections else 0
    if conn_count >= 1:
        score += 0.3
        reasons.append(f"Contains {conn_count} architectural relationship/data-flow connections.")
    return score


def _score_ocr_text(content: ExtractedAttachmentContent, reasons: List[str], keywords_found: List[str]) -> float:
    score = 0.0
    raw_text = (content.extracted_text or "").lower()
    if len(raw_text.strip()) < 50:
        return score

    for kw in TECHNICAL_INDICATORS:
        if kw in raw_text:
            keywords_found.append(kw)
    if len(keywords_found) >= 3:
        score += 0.3
        reasons.append(f"High technical keyword density: {', '.join(keywords_found[:5])}.")
    elif len(keywords_found) >= 1:
        score += 0.15
        reasons.append(f"Contains technical terms: {', '.join(keywords_found[:3])}.")

    metric_matches = NUMERIC_METRIC_PATTERN.findall(raw_text)
    if metric_matches:
        score += 0.2
        reasons.append(f"Contains quantitative metrics: {', '.join(metric_matches[:4])}.")
    return score


def _resolve_tier(score: float, reasons: List[str]) -> Tuple[CollateralValueTier, bool]:
    if score >= 0.5:
        return CollateralValueTier.HIGH_VALUE, True
    if score >= 0.3:
        return CollateralValueTier.MEDIUM_VALUE, True
    reasons.append("Below substantive threshold for knowledge graph mutation.")
    return CollateralValueTier.LOW_VALUE, False


def classify_collateral_value(
    record: AttachmentRecord,
    content: Optional[ExtractedAttachmentContent] = None,
) -> CollateralValueAssessment:
    """
    Evaluates whether an extracted screenshot or diagram has sufficient substantive
    value to warrant executive narrative synthesis in Output 02 and entity mutations
    in Output 03.
    """
    reasons: List[str] = []
    keywords_found: List[str] = []

    if not content:
        return CollateralValueAssessment(
            tier=CollateralValueTier.FAILED_EXTRACTION,
            is_value_added=False,
            score=0.0,
            reasons=["No extracted content or OCR text available."],
            technical_keywords_found=[],
            extraction_status="EMPTY",
            error_reason="No extracted content available",
            is_failed=True,
        )

    if getattr(content, "is_failed", False) or getattr(content, "extraction_status", "") == "FAILED":
        err_msg = content.error_reason or "Visual extraction failed or exhausted"
        return CollateralValueAssessment(
            tier=CollateralValueTier.FAILED_EXTRACTION,
            is_value_added=False,
            score=0.0,
            reasons=[f"Extraction failed: {err_msg}"],
            technical_keywords_found=[],
            extraction_status="FAILED",
            error_reason=err_msg,
            is_failed=True,
        )

    score = _score_components_and_connections(content, reasons)
    score += _score_ocr_text(content, reasons, keywords_found)

    if getattr(content, "tables", None):
        score += 0.25
        reasons.append(f"Contains {len(content.tables)} structured data or status table(s).")

    if getattr(content, "milestones", None):
        score += 0.25
        reasons.append(f"Contains {len(content.milestones)} dated milestone(s).")

    if getattr(content, "is_demo_or_sample", False):
        reasons.append("Identified as demo or illustrative mockup data; excluded from canonical facts.")

    if getattr(content, "has_diagrams", False):
        score += 0.2
        reasons.append("Identified as architectural or system diagram.")

    score = min(1.0, score)
    tier, is_value_added = _resolve_tier(score, reasons)

    return CollateralValueAssessment(
        tier=tier,
        is_value_added=is_value_added,
        score=round(score, 2),
        reasons=reasons,
        technical_keywords_found=keywords_found,
        extraction_status=content.extraction_status,
        error_reason=content.error_reason,
        is_failed=False,
    )
