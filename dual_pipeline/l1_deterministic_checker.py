"""L1 Deterministic Verifier for Sealed L2 Observations.

Pure deterministic function that verifies objective execution facts of the
sealed L2 observation against the L1 plan before semantic sufficiency reasoning.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from dual_pipeline.core import digest
from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    DeterministicReasonCode,
)

_CITATION_KEYS = frozenset({"citations", "citation_ids", "evidence_ids", "sources"})
_VALID_DISPOSITIONS = frozenset({"primary", "repaired", "escalated", "skipped"})


def _collect_from_value(v: Any, citations: set[str]) -> None:
    if isinstance(v, (list, tuple, set)):
        for c in v:
            if isinstance(c, str) and c.strip():
                citations.add(c.strip())
    elif isinstance(v, str) and v.strip():
        citations.add(v.strip())


def _extract_all_citations(obj: Any) -> set[str]:
    """Recursively collect citation identifiers from structured answer."""
    citations: set[str] = set()
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if k in _CITATION_KEYS:
                _collect_from_value(v, citations)
            else:
                citations.update(_extract_all_citations(v))
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            citations.update(_extract_all_citations(item))
    return citations


def _add_ids_from_mapping(mapping: Mapping[str, Any], valid_ids: set[str]) -> None:
    for key in ("record_id", "evidence_id", "id"):
        val = mapping.get(key)
        if val:
            valid_ids.add(str(val))
    eids = mapping.get("evidence_ids")
    if isinstance(eids, (list, tuple, set)):
        for eid in eids:
            if eid:
                valid_ids.add(str(eid))


def _extract_packet_identifiers(evidence_packet: Mapping[str, Any]) -> set[str]:
    """Extract all available record and evidence IDs from evidence packet."""
    valid_ids: set[str] = set()
    lanes = ("approved", "candidate", "external_public", "counterevidence")
    for lane in lanes:
        lane_items = evidence_packet.get(lane, [])
        if not isinstance(lane_items, Sequence):
            continue
        for item in lane_items:
            if isinstance(item, Mapping):
                _add_ids_from_mapping(item, valid_ids)
                rec = item.get("record")
                if isinstance(rec, Mapping):
                    _add_ids_from_mapping(rec, valid_ids)
    return valid_ids


def _check_disposition(disposition: str) -> DeterministicCheckItem:
    if disposition in _VALID_DISPOSITIONS:
        return DeterministicCheckItem(
            name="l2_execution_disposition",
            passed=True,
            details=f"Disposition is valid: '{disposition}'",
        )
    return DeterministicCheckItem(
        name="l2_execution_disposition",
        passed=False,
        details=f"Disposition '{disposition}' is failed or unrecognized",
        reason_code=DeterministicReasonCode.L2_EXECUTION_FAILED.value,
    )


def _check_sealed_integrity(sealed_l2: Mapping[str, Any]) -> DeterministicCheckItem:
    sealed_digest = sealed_l2.get("canonical_digest")
    if not (isinstance(sealed_digest, Mapping) and "value" in sealed_digest):
        return DeterministicCheckItem(
            name="sealed_artifact_integrity",
            passed=False,
            details="Missing or invalid canonical_digest in sealed L2 artifact",
            reason_code=DeterministicReasonCode.CANONICAL_DIGEST_MISMATCH.value,
        )
    body_to_hash = {k: v for k, v in sealed_l2.items() if k != "canonical_digest"}
    computed = digest(body_to_hash)
    if computed == sealed_digest["value"]:
        return DeterministicCheckItem(
            name="sealed_artifact_integrity",
            passed=True,
            details="Canonical digest verified against artifact body",
        )
    return DeterministicCheckItem(
        name="sealed_artifact_integrity",
        passed=False,
        details=f"Digest mismatch: recorded '{sealed_digest['value'][:12]}...' vs computed '{computed[:12]}...'",
        reason_code=DeterministicReasonCode.CANONICAL_DIGEST_MISMATCH.value,
    )


def _check_answer_presence(
    answer: Optional[Mapping[str, Any]], is_skipped: bool
) -> DeterministicCheckItem:
    if answer is not None:
        return DeterministicCheckItem(
            name="answer_presence",
            passed=True,
            details="Observation payload contains non-null answer",
        )
    if is_skipped:
        return DeterministicCheckItem(
            name="answer_presence",
            passed=True,
            details="Observation has null answer as expected for skipped execution",
        )
    return DeterministicCheckItem(
        name="answer_presence",
        passed=False,
        details="Observation has null answer but execution was not skipped",
        reason_code=DeterministicReasonCode.ANSWER_MISSING.value,
    )


def _find_missing_query_fields(query_type: str, answer: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    if query_type == "opportunity_nba" and "opportunities" not in answer:
        missing.append("opportunities")
    elif query_type == "gap_risk_inconsistency" and not any(
        k in answer for k in ("gaps", "risks", "inconsistencies", "items", "pain_points", "current_state")
    ):
        missing.append("gaps/risks")
    elif query_type == "relationship_explanation" and not any(
        k in answer for k in ("relationship", "explanation", "entities", "paths", "summary", "current_state")
    ):
        missing.append("relationship/explanation")
    return missing


def _check_schema_validity(
    answer: Optional[Mapping[str, Any]], plan: Mapping[str, Any]
) -> Optional[DeterministicCheckItem]:
    if answer is None:
        return None
    query_type = str(plan.get("query_type", "exact_lookup"))
    if not isinstance(answer, Mapping) or len(answer) == 0:
        return DeterministicCheckItem(
            name="output_schema_validity",
            passed=False,
            details="Answer is empty or not a valid dictionary mapping",
            reason_code=DeterministicReasonCode.SCHEMA_INVALID.value,
        )
    missing = _find_missing_query_fields(query_type, answer)
    if missing:
        return DeterministicCheckItem(
            name="output_schema_validity",
            passed=False,
            details=f"Missing expected output fields for {query_type}: {missing}",
            reason_code=DeterministicReasonCode.REQUIRED_FIELDS_MISSING.value,
        )
    return DeterministicCheckItem(
        name="output_schema_validity",
        passed=True,
        details=f"Output satisfies structural requirements for query_type '{query_type}'",
    )


def _check_citations_resolution(
    answer: Optional[Mapping[str, Any]], evidence_packet: Optional[Mapping[str, Any]]
) -> Optional[DeterministicCheckItem]:
    if answer is None:
        return None
    if evidence_packet is None:
        return DeterministicCheckItem(
            name="citations_resolution",
            passed=False,
            details="Cannot verify citations: evidence packet not provided",
            reason_code=DeterministicReasonCode.EVIDENCE_PACKET_MISSING.value,
        )
    cited_ids = _extract_all_citations(answer)
    valid_packet_ids = _extract_packet_identifiers(evidence_packet)
    unresolved = sorted(cited_ids - valid_packet_ids)
    if unresolved:
        return DeterministicCheckItem(
            name="citations_resolution",
            passed=False,
            details=f"Found {len(unresolved)} unresolved citations: {unresolved[:5]}",
            reason_code=DeterministicReasonCode.CITATIONS_UNRESOLVED.value,
        )
    return DeterministicCheckItem(
        name="citations_resolution",
        passed=True,
        details=f"All {len(cited_ids)} citations resolve to verified packet records",
    )


def _check_claim_grounding(
    claim_evidence_map: Optional[Mapping[str, Any]]
) -> Optional[DeterministicCheckItem]:
    if claim_evidence_map is None:
        return None
    claims = claim_evidence_map.get("claims", [])
    ungrounded = [
        c.get("claim_text", "unknown")
        for c in claims
        if isinstance(c, Mapping) and c.get("status") != "grounded"
    ]
    if ungrounded:
        return DeterministicCheckItem(
            name="claim_grounding",
            passed=False,
            details=f"{len(ungrounded)} ungrounded claims detected in claim_evidence_map",
            reason_code=DeterministicReasonCode.CLAIMS_UNGROUNDED.value,
        )
    return DeterministicCheckItem(
        name="claim_grounding",
        passed=True,
        details=f"All {len(claims)} claims verified as grounded",
    )


def _check_graph_immutability(
    graph_hash_before: Optional[str], graph_hash_after: Optional[str]
) -> Optional[DeterministicCheckItem]:
    if graph_hash_before is None or graph_hash_after is None:
        return None
    if graph_hash_before == graph_hash_after:
        return DeterministicCheckItem(
            name="graph_immutability",
            passed=True,
            details="Graph hash unchanged across reasoning lifecycle",
        )
    return DeterministicCheckItem(
        name="graph_immutability",
        passed=False,
        details=f"Graph hash mutated during query: before '{graph_hash_before[:12]}...' != after '{graph_hash_after[:12]}...'",
        reason_code=DeterministicReasonCode.GRAPH_MUTATION_VIOLATION.value,
    )


def _check_acceptance_criteria(
    plan: Mapping[str, Any], answer: Optional[Mapping[str, Any]]
) -> Optional[DeterministicCheckItem]:
    acceptance_criteria = plan.get("acceptance_criteria")
    if not (isinstance(acceptance_criteria, Mapping) and answer is not None):
        return None
    min_citations = acceptance_criteria.get("min_citations", 0)
    if min_citations > 0:
        cited_ids = _extract_all_citations(answer)
        if len(cited_ids) < min_citations:
            return DeterministicCheckItem(
                name="plan_acceptance_criteria",
                passed=False,
                details=f"Unmet acceptance criteria: ['min_citations={min_citations}, got {len(cited_ids)}']",
                reason_code=DeterministicReasonCode.ACCEPTANCE_CRITERIA_UNMET.value,
            )
    return DeterministicCheckItem(
        name="plan_acceptance_criteria",
        passed=True,
        details="All explicit plan acceptance criteria satisfied",
    )


def run_l1_deterministic_checks(
    *,
    plan: Mapping[str, Any],
    sealed_l2: Mapping[str, Any],
    answer: Optional[Mapping[str, Any]],
    evidence_packet: Optional[Mapping[str, Any]] = None,
    claim_evidence_map: Optional[Mapping[str, Any]] = None,
    graph_hash_before: Optional[str] = None,
    graph_hash_after: Optional[str] = None,
) -> tuple[bool, list[DeterministicCheckItem]]:
    """Execute all objective post-tool execution checks for L1.

    Returns:
        (all_passed: bool, checks: list[DeterministicCheckItem])
    """
    disposition = str(sealed_l2.get("l2_disposition", "unknown")).lower()
    checks: list[DeterministicCheckItem] = [
        _check_disposition(disposition),
        _check_sealed_integrity(sealed_l2),
        _check_answer_presence(answer, disposition == "skipped"),
    ]

    optional_checks = [
        _check_schema_validity(answer, plan),
        _check_citations_resolution(answer, evidence_packet),
        _check_claim_grounding(claim_evidence_map),
        _check_graph_immutability(graph_hash_before, graph_hash_after),
        _check_acceptance_criteria(plan, answer),
    ]
    for chk in optional_checks:
        if chk is not None:
            checks.append(chk)

    all_passed = all(c.passed for c in checks)
    return all_passed, checks
