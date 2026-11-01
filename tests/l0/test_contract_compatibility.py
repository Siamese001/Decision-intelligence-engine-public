"""Test suite for Layer 0 contract models, adapters, and schema compatibility."""
from __future__ import annotations

import time

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    EvidenceRecord,
    FastPathContext,
    TerminalState,
)
from dual_pipeline.l0.validation import (
    validate_canonical_graph_delta,
    validate_evidence_record,
    validate_fast_path_context,
    validate_monotonic_state_transition,
)


def test_evidence_record_validation():
    """Verifies that an EvidenceRecord validates correctly."""
    rec = EvidenceRecord(
        evidence_id="ev_001",
        session_id="sess_001",
        source_id="src_001",
        speaker="Amit Ayer",
        text="Deploy the AWS S3 pipeline architecture.",
        timestamp=time.time(),
        namespace=CLIENT_SAFE_NAMESPACE,
        tenant_id="partner_advisory",
        digest="d1a2b3c4d5e6",
    )
    res = validate_evidence_record(rec)
    assert res.is_valid, f"Expected valid, got {res.errors}"


def test_evidence_record_empty_text_fails():
    """Verifies that empty evidence text fails closed."""
    rec = EvidenceRecord(
        evidence_id="ev_002",
        session_id="sess_001",
        source_id="src_001",
        speaker="Speaker",
        text="   ",
        timestamp=time.time(),
        namespace=CLIENT_SAFE_NAMESPACE,
        tenant_id="partner_advisory",
    )
    res = validate_evidence_record(rec)
    assert not res.is_valid
    assert any("empty" in e for e in res.errors)


def test_fast_path_context_law_3_enforcement():
    """Verifies that FastPathContext enforces Law 3 untrusted fast path."""
    fast = FastPathContext(
        event_id="fp_001",
        meeting_id="meet_001",
        speaker="Speaker 1",
        text="We might use Terraform.",
        start_ts=10.0,
        end_ts=12.5,
    )
    res = validate_fast_path_context(fast)
    assert res.is_valid
    assert "[UNTRUSTED_FAST_PATH]" in fast.badge
    assert fast.is_ephemeral is True


def test_canonical_graph_delta_prevents_advisory_leakage():
    """Enforces Law 1 & C-19: internal advisory nodes cannot leak into canonical nodes."""
    delta = CanonicalGraphDelta(
        delta_id="delta_001",
        source_evidence_ids=["ev_001"],
        nodes_created=[
            CanonicalGraphNode(
                node_id="node_adv_leaked",
                evidence_id="ev_001",
                namespace=INTERNAL_ADVISORY_NAMESPACE,  # LEAK!
                name="Internal Political Dynamic",
            )
        ],
    )
    res = validate_canonical_graph_delta(delta)
    assert not res.is_valid
    assert any("leaked" in e for e in res.errors)


def test_monotonic_state_transitions():
    """Verifies valid and invalid lifecycle state transitions."""
    # Valid: IN_PROGRESS -> DECISION_PRODUCED
    res1 = validate_monotonic_state_transition(
        TerminalState.IN_PROGRESS.value,
        TerminalState.DECISION_PRODUCED.value,
    )
    assert res1.is_valid

    # Invalid: DECISION_PRODUCED -> IN_PROGRESS (Regression!)
    res2 = validate_monotonic_state_transition(
        TerminalState.DECISION_PRODUCED.value,
        TerminalState.IN_PROGRESS.value,
    )
    assert not res2.is_valid
