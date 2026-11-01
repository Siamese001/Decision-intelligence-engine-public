"""
telemetry_core/events.py — Specialized OpenTelemetry domain event helpers.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

logger = logging.getLogger(__name__)


def record_eval_result(
    span: trace.Span,
    gate_id: str,
    verdict: str,
    score: Optional[Union[int, float]] = None,
    is_mock: bool = False,
    extra_attributes: Optional[Dict[str, Any]] = None,
) -> None:
    """Records evaluation gate verdict and rubric metrics onto a span."""
    span.set_attribute("eval.gate_id", gate_id)
    span.set_attribute("eval.verdict", verdict)
    span.set_attribute("eval.is_mock", is_mock)
    if score is not None:
        span.set_attribute("eval.score", float(score))
    if extra_attributes:
        for k, v in extra_attributes.items():
            if isinstance(v, (str, int, float, bool)):
                span.set_attribute(f"eval.{k}", v)


def record_quarantine_event(
    span: trace.Span,
    node_id: str,
    reason: str,
    rubric_scores: Optional[Dict[str, Any]] = None,
) -> None:
    """Emits an immutable QuarantineEvent on the active span when a candidate fails QA."""
    attrs: Dict[str, Any] = {
        "quarantine.node_id": node_id,
        "quarantine.reason": reason,
    }
    if rubric_scores:
        for k, v in rubric_scores.items():
            attrs[f"quarantine.score.{k}"] = str(v)
    span.add_event("QuarantineEvent", attributes=attrs)


def record_bipartite_leak_event(
    span: trace.Span,
    requested_namespace: str,
    leaked_node_id: str,
    node_tags: Optional[List[str]] = None,
) -> None:
    """Emits a critical security event when an AGENTIC_LEVERAGE node breaches the CLIENT_SAFE boundary."""
    attrs = {
        "security.violation_type": "BIPARTITE_NAMESPACE_LEAK",
        "security.requested_namespace": requested_namespace,
        "security.leaked_node_id": leaked_node_id,
        "security.node_tags": str(node_tags or []),
    }
    span.add_event("security.bipartite_leak_detected", attributes=attrs)
    span.set_status(
        Status(
            StatusCode.ERROR,
            f"Bipartite namespace breach: node {leaked_node_id} leaked into {requested_namespace}",
        )
    )

    # Immutable security audit entry
    try:
        import json
        import os

        from dual_pipeline.core import ROOT, digest, utc_now
        audit_entry = {
            "event": "BIPARTITE_NAMESPACE_LEAK",
            "timestamp": utc_now(),
            "pid": os.getpid(),
            "requested_namespace": requested_namespace,
            "leaked_node_id": leaked_node_id,
            "node_tags": node_tags or [],
            "severity": "CRITICAL",
        }
        audit_entry["entry_digest"] = digest(audit_entry)
        audit_file = ROOT / ".local" / "dual_pipeline" / "security_audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with audit_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(audit_entry) + "\n")

        from config.credentials import CredentialManager
        webhook_url = CredentialManager.get_credential("SECURITY_WEBHOOK_URL")
        if webhook_url:
            import urllib.request
            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(audit_entry).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
    except Exception as e:
        logger.error("Could not record security audit trail: %s", e)
        raise RuntimeError(f"Fail-Fast: Could not record security audit trail: {e}") from e


def record_phonetic_repair_event(
    span: trace.Span,
    original_token: str,
    resolved_speaker_id: str,
    confidence: float,
    resolution_basis: str = "acoustic_lexical_roster",
) -> None:
    """Emits a phonetic_repair event during Stage L1 / ALR identity resolution."""
    span.add_event(
        "phonetic_repair",
        attributes={
            "alr.original_token": original_token,
            "alr.resolved_speaker_id": resolved_speaker_id,
            "alr.confidence": float(confidence),
            "alr.resolution_basis": resolution_basis,
        },
    )


def record_l1_post_l2_review_event(
    span: trace.Span,
    iteration: int,
    verdict: str,
    deterministic_passed: bool,
    semantic_passed: bool,
    replan_directive: Optional[Dict[str, Any]] = None,
) -> None:
    """Emits an L1PostL2ReviewEvent capturing the feedback evaluation."""
    attrs: Dict[str, Any] = {
        "l1_review.iteration": iteration,
        "l1_review.verdict": verdict,
        "l1_review.deterministic_passed": deterministic_passed,
        "l1_review.semantic_passed": semantic_passed,
    }
    if replan_directive:
        attrs["l1_review.has_replan_directive"] = True
        attrs["l1_review.replan_type"] = replan_directive.get("directive_type", "GENERIC")
    span.add_event("L1PostL2ReviewEvent", attributes=attrs)


def record_l1_replan_cycle_event(
    span: trace.Span,
    iteration: int,
    reason: str,
    unanswered_subquestions: Optional[List[str]] = None,
) -> None:
    """Emits an L1ReplanCycleEvent when execution loops back for re-planning."""
    attrs: Dict[str, Any] = {
        "l1_replan.iteration": iteration,
        "l1_replan.reason": reason,
        "l1_replan.unanswered_count": len(unanswered_subquestions or []),
    }
    span.add_event("L1ReplanCycleEvent", attributes=attrs)


__all__ = [
    "record_eval_result",
    "record_quarantine_event",
    "record_bipartite_leak_event",
    "record_phonetic_repair_event",
    "record_l1_post_l2_review_event",
    "record_l1_replan_cycle_event",
]

