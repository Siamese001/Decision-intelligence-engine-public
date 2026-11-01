"""Dual-Pipeline Layer 0: Schema, State-Transition & Invariant Validation.

Enforces deterministic schema conformance, monotonic lifecycle progression,
and security containment without third-party dependencies.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import re
from typing import List

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    AdvisoryCategory,
    CanonicalGraphDelta,
    EvidenceRecord,
    FastPathContext,
    HandoffEnvelope,
    TerminalState,
    ValidationResult,
)

# Date regex for strict MM/DD/YYYY validation
RE_DATE_MM_DD_YYYY = re.compile(r"^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/\d{4}$")


def validate_evidence_record(record: EvidenceRecord) -> ValidationResult:
    """Validates structural correctness and provenance bounds of an EvidenceRecord."""
    errors: List[str] = []
    warnings: List[str] = []
    rules: List[str] = ["R-EV-01", "R-EV-02", "R-EV-03"]

    if not record.evidence_id:
        errors.append("evidence_id must not be empty.")
    if not record.text or not record.text.strip():
        errors.append("Evidence text must not be empty.")
    if record.namespace not in (CLIENT_SAFE_NAMESPACE, INTERNAL_ADVISORY_NAMESPACE):
        errors.append(f"Invalid namespace '{record.namespace}'.")
    if not record.tenant_id:
        errors.append("tenant_id must be assigned.")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        rule_ids=rules,
    )


def validate_fast_path_context(context: FastPathContext) -> ValidationResult:
    """Enforces Law 3: Fast Path context must remain strictly ephemeral and read-only."""
    errors: List[str] = []
    warnings: List[str] = []
    rules: List[str] = ["LAW-3-FAST-PATH"]

    if not context.is_ephemeral:
        errors.append("FastPathContext must have is_ephemeral=True (Law 3).")
    if "[UNTRUSTED_FAST_PATH]" not in context.badge:
        errors.append("FastPathContext must carry '[UNTRUSTED_FAST_PATH]' badge.")
    if not context.text:
        warnings.append("FastPathContext contains empty text.")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        rule_ids=rules,
    )


def validate_canonical_graph_delta(delta: CanonicalGraphDelta) -> ValidationResult:
    """Validates that a CanonicalGraphDelta satisfies C-01 to C-18 graph invariants."""
    errors: List[str] = []
    warnings: List[str] = []
    rules: List[str] = ["C-01", "C-02", "C-03", "C-19"]

    if not delta.delta_id:
        errors.append("delta_id must not be empty.")
    if not delta.source_evidence_ids:
        errors.append("source_evidence_ids must link to at least one evidence item.")

    # Validate node names and namespaces
    for node in delta.nodes_created + delta.nodes_updated:
        if not node.node_id:
            errors.append("Graph node has empty node_id.")
        if not node.name:
            warnings.append(f"Graph node {node.node_id} has empty name.")
        if node.namespace == INTERNAL_ADVISORY_NAMESPACE:
            errors.append(
                f"Internal advisory node {node.node_id} leaked into canonical nodes (Law 1/C-19)."
            )

    # Validate internal advisory nodes
    for adv in delta.advisory_nodes:
        if adv.category not in [c.value for c in AdvisoryCategory]:
            errors.append(f"Advisory node {adv.node_id} has invalid category '{adv.category}' (Law 2).")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        rule_ids=rules,
    )


def validate_handoff_envelope(envelope: HandoffEnvelope) -> ValidationResult:
    """Validates inter-pipeline transport envelope integrity."""
    errors: List[str] = []
    rules: List[str] = ["R-ENV-01", "R-ENV-02"]

    if not envelope.envelope_id:
        errors.append("envelope_id is empty.")
    if not envelope.source_pipeline or not envelope.target_pipeline:
        errors.append("Source and target pipelines must be defined.")
    if not envelope.claim_check or not envelope.claim_check.digest:
        errors.append("Envelope claim_check must carry a valid cryptographic digest.")

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        rule_ids=rules,
    )


def validate_monotonic_state_transition(
    current_state: str,
    next_state: str,
) -> ValidationResult:
    """Enforces valid monotonic state transitions for decision lifecycles."""
    valid_transitions = {
        TerminalState.IN_PROGRESS.value: [
            TerminalState.DECISION_PRODUCED.value,
            TerminalState.CLARIFICATION_REQUIRED.value,
            TerminalState.ABSTENTION.value,
            TerminalState.EVIDENCE_READY_DECISION_NOT_PRODUCED.value,
        ],
        TerminalState.DECISION_PRODUCED.value: [],
        TerminalState.CLARIFICATION_REQUIRED.value: [
            TerminalState.IN_PROGRESS.value,
            TerminalState.DECISION_PRODUCED.value,
        ],
        TerminalState.ABSTENTION.value: [],
        TerminalState.EVIDENCE_READY_DECISION_NOT_PRODUCED.value: [
            TerminalState.IN_PROGRESS.value,
            TerminalState.DECISION_PRODUCED.value,
        ],
    }

    errors: List[str] = []
    allowed = valid_transitions.get(current_state, [])
    if next_state not in allowed:
        errors.append(
            f"Illegal state transition from '{current_state}' to '{next_state}'."
        )

    return ValidationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        rule_ids=["MONOTONIC_TRANSITION"],
    )
