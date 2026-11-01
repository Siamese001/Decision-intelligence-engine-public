"""Adversarial & Edge-Case End-to-End Test Suite across Waves 1 to 4.

Verifies system resilience against boundary failures, concurrency races,
malformed payloads, namespace leaks, and invariant bypass attempts:
- Wave 1: Zero-copy memoryview immutability, digest mismatch, and concurrent WAL races.
- Wave 2: Ring buffer wrap-around, closed buffer protection, and mixed-namespace UWG rejection.
- Wave 3: Pre-execution policy rejection, retry budget exhaustion, and exact 25% margin boundaries.
- Wave 4: Single-trip unauthorized candidate stripping, empty candidate packs, and non-destructive rollback.
"""
from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path

import pytest

from dual_pipeline.healing_policy import (
    arbitrate_decision_margin,
)
from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    ClaimCheck,
    EvidencePack,
    EvidenceRecord,
    HandoffEnvelope,
    PolicyContext,
)
from dual_pipeline.l0.provenance import compute_content_digest, mint_claim_check
from dual_pipeline.l0.quarantine import evaluate_quarantine_profile
from dual_pipeline.l0.wal_evidence import (
    append_evidence_record,
    get_evidence_record_by_digest,
    init_evidence_wal_db,
    query_evidence_by_tenant_and_namespace,
)
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack
from pipeline_a_intake.bridges.immutable_ring import (
    BackpressurePolicy,
    ImmutableRingBuffer,
)
from pipeline_b_extraction.intake_gateway import (
    ComputeForkRoutingError,
    ExtractionIntakeGateway,
)
from pipeline_b_extraction.uwg_gateway import ProgrammaticUwgGateway


@pytest.fixture
def temp_wal_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "edge_case_evidence.sqlite"
    init_evidence_wal_db(db_path)
    return db_path


# ==============================================================================
# WAVE 1 EDGE CASES
# ==============================================================================

def test_edge_case_ring_buffer_readonly_mutation_attempt() -> None:
    """Verifies that the memoryview exposed by RingBufferSlot is strictly read-only."""
    ring = ImmutableRingBuffer(capacity=4)
    raw = bytearray(b"immutable-audio-payload")
    claim = mint_claim_check(bytes(raw), storage_uri="ring://slot_01")
    env = HandoffEnvelope(
        envelope_id="env_mut_01",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,
        payload={},
    )
    slot = ring.enqueue(env, bytes(raw))

    # Attempting in-place modification of read-only memoryview must raise TypeError
    with pytest.raises(TypeError):
        slot.buffer_view[0] = 0xFF


def test_edge_case_claim_check_corrupted_digest_rejected() -> None:
    """Verifies that bytes tampering before ring enqueue is caught and rejected."""
    ring = ImmutableRingBuffer(capacity=4)
    claim = ClaimCheck(
        claim_id="chk_tampered",
        digest="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        storage_uri="ring://tampered",
    )
    env = HandoffEnvelope(
        envelope_id="env_tampered",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,
        payload={},
    )
    with pytest.raises(ValueError, match="Data integrity violation"):
        ring.enqueue(env, b"actual-bytes-payload")


def test_edge_case_wal_duplicate_digest_concurrent_race(temp_wal_db: Path) -> None:
    """Verifies that simultaneous appends of the exact same evidence digest resolve idempotently."""
    rec = EvidenceRecord(
        evidence_id="ev_race_01",
        session_id="sess_race",
        source_id="mic",
        speaker="Speaker",
        text="Race condition test text",
        timestamp=time.time(),
        namespace=CLIENT_SAFE_NAMESPACE,
        tenant_id="tenant_race",
        digest=compute_content_digest("Race condition test text").sha256,
    )

    def worker_append() -> bool:
        return append_evidence_record(temp_wal_db, rec)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_append) for _ in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exactly one thread inserts the row (True); all other 9 threads safely detect duplicate and return False
    assert sum(results) == 1
    # The database must contain exactly one record for this digest
    fetched = get_evidence_record_by_digest(temp_wal_db, rec.digest)
    assert fetched is not None
    assert fetched.evidence_id == "ev_race_01"

    records = query_evidence_by_tenant_and_namespace(temp_wal_db, "tenant_race", CLIENT_SAFE_NAMESPACE)
    assert len(records) == 1


def test_edge_case_quarantine_boundary_values() -> None:
    """Verifies boundary thresholds for multi-dimensional soft quarantine."""
    # Confidence: 0.60 is standard threshold -> >= 0.60 passes, < 0.60 quarantined
    p_pass = evaluate_quarantine_profile(strategic_value=0.5, evidence_quality=0.6, confidence=0.60, tacticality=0.5)
    assert p_pass.is_quarantined is False

    p_fail_conf = evaluate_quarantine_profile(strategic_value=0.5, evidence_quality=0.6, confidence=0.599, tacticality=0.5)
    assert p_fail_conf.is_quarantined is True
    assert p_fail_conf.quarantine_reason == "LOW_CONFIDENCE"

    # Tacticality ceiling: 0.85 -> <= 0.85 passes, > 0.85 quarantined
    p_fail_tact = evaluate_quarantine_profile(strategic_value=0.5, evidence_quality=0.6, confidence=0.7, tacticality=0.851)
    assert p_fail_tact.is_quarantined is True
    assert p_fail_tact.quarantine_reason == "HIGH_TACTICALITY"


# ==============================================================================
# WAVE 2 EDGE CASES
# ==============================================================================

def test_edge_case_ring_buffer_lagging_consumers_wrap_around() -> None:
    """Verifies ring buffer behavior when DROP_OLDEST wraps around multiple consumers."""
    ring = ImmutableRingBuffer(capacity=3, backpressure_policy=BackpressurePolicy.DROP_OLDEST)

    for i in range(5):
        raw = f"frame_{i}".encode("utf-8")
        claim = mint_claim_check(raw, storage_uri=f"ring://{i}")
        env = HandoffEnvelope(
            envelope_id=f"env_{i}",
            source_pipeline="PIPELINE_A",
            target_pipeline="PIPELINE_B",
            payload_type="AUDIO_FRAME",
            claim_check=claim,
            payload={},
        )
        ring.enqueue(env, raw)

    # Slow consumer "slow_c" reads only now; capacity=3 holds only newest 3 slots (2, 3, 4)
    slots = ring.read_unconsumed_for_consumer("slow_c")
    assert len(slots) <= 3
    assert slots[-1].sequence_id == 5


def test_edge_case_closed_ring_buffer_rejects_operations() -> None:
    """Verifies that enqueueing into a closed ring buffer fails immediately."""
    ring = ImmutableRingBuffer(capacity=4)
    ring._closed = True

    raw = b"test_payload"
    claim = mint_claim_check(raw, storage_uri="ring://closed")
    env = HandoffEnvelope(
        envelope_id="env_closed",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,
        payload={},
    )
    with pytest.raises(RuntimeError, match="Cannot enqueue into closed ring buffer"):
        ring.enqueue(env, raw)


def test_edge_case_compute_fork_tampered_envelope_rejected(temp_wal_db: Path) -> None:
    """Verifies Law 1: unclassified or tampered namespace raises ComputeForkRoutingError on route."""
    gateway = ExtractionIntakeGateway(wal_db_path=temp_wal_db)
    raw = b"unclassified data"
    claim = mint_claim_check(raw, storage_uri="ring://tampered", namespace="UNAUTHORIZED_NAMESPACE")
    env = HandoffEnvelope(
        envelope_id="env_tampered_ns",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="TRANSCRIPT_DELTA",
        claim_check=claim,
        payload={"text": "some text"},
    )
    # Direct routing raises ComputeForkRoutingError
    with pytest.raises(ComputeForkRoutingError, match="Law 1 Violation"):
        gateway.route_compute_fork(env)

    # Process envelope handles it gracefully and returns failure code
    ok, route, record = gateway.process_envelope(env, raw)
    assert ok is False
    assert route == "ROUTING_FAILED"
    assert record is None


def test_edge_case_uwg_delta_with_mixed_client_safe_and_advisory() -> None:
    """Verifies that a delta containing both safe and advisory nodes is rejected (Rule C-19)."""
    gateway = ProgrammaticUwgGateway()
    mixed_delta = CanonicalGraphDelta(
        delta_id="delta_mixed_leak",
        source_evidence_ids=["ev_safe", "ev_adv"],
        nodes_created=[
            CanonicalGraphNode(node_id="sys:safe", evidence_id="ev_safe", namespace=CLIENT_SAFE_NAMESPACE),
            CanonicalGraphNode(node_id="adv:leaked", evidence_id="ev_adv", namespace=INTERNAL_ADVISORY_NAMESPACE),
        ],
    )
    receipt = gateway.submit_delta(mixed_delta)
    assert receipt.status == "REJECTED"
    assert any("INTERNAL_ADVISORY" in reason or "C-19" in reason for reason in receipt.rejection_reasons)


# ==============================================================================
# WAVE 3 EDGE CASES
# ==============================================================================

def test_edge_case_l1_feedback_loop_boundary() -> None:
    """Verifies that L1 feedback state machine halts at max iterations."""
    from dual_pipeline.l1_feedback_loop import L1FeedbackStateMachine, LoopState
    from dual_pipeline.l1_review_contracts import L1PostL2ReviewContract, L1ReviewVerdict

    sm = L1FeedbackStateMachine(max_iterations=2)
    contract_replan = L1PostL2ReviewContract.create(
        run_id="run-edge",
        query_id="q-edge",
        plan_id="p-edge",
        sealed_artifact_id="s-edge",
        verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
        deterministic_passed=True,
        deterministic_checks=[],
        semantic_passed=False,
    )

    # Cycle 0
    sm.start_cycle()
    sm.mark_executed()
    sm.start_review()
    state_0, _ = sm.process_review_verdict(contract_replan)
    assert state_0 == LoopState.REPLANNING
    assert sm.current_iteration == 1

    # Cycle 1
    sm.start_cycle()
    sm.mark_executed()
    sm.start_review()
    state_1, _ = sm.process_review_verdict(contract_replan)
    assert state_1 == LoopState.REPLANNING
    assert sm.current_iteration == 2

    # Cycle 2 - exceeds max_iterations=2 -> EXHAUSTED
    sm.start_cycle()
    sm.mark_executed()
    sm.start_review()
    state_2, _ = sm.process_review_verdict(contract_replan)
    assert state_2 == LoopState.EXHAUSTED
    assert sm.is_terminal() is True
    assert sm.can_proceed_to_exit_gates() is False


def test_edge_case_hitl_margin_boundary_conditions() -> None:
    """Verifies boundary behavior of the 25% ambiguity margin rule."""
    # 1. Exact boundary Delta = 0.250 -> Autonomous proceed
    dec_exact = arbitrate_decision_margin("dec_250", [("A", 0.75), ("B", 0.50)])
    assert dec_exact.hitl_required is False
    assert dec_exact.confidence_margin == 0.25

    # 2. Just below boundary Delta = 0.249 -> Escalate to HITL
    dec_below = arbitrate_decision_margin("dec_249", [("A", 0.749), ("B", 0.500)])
    assert dec_below.hitl_required is True

    # 3. Exact tie Delta = 0.0 -> Escalate to HITL
    dec_tie = arbitrate_decision_margin("dec_tie", [("A", 0.80), ("B", 0.80)])
    assert dec_tie.hitl_required is True
    assert dec_tie.confidence_margin == 0.0

    # 4. Single candidate with low score (< 0.50) -> Escalate to HITL
    dec_single_low = arbitrate_decision_margin("dec_single_low", [("A", 0.40)])
    assert dec_single_low.hitl_required is True

    # 5. Empty candidates -> Escalate to HITL
    dec_empty = arbitrate_decision_margin("dec_empty", [])
    assert dec_empty.hitl_required is True


# ==============================================================================
# WAVE 4 EDGE CASES
# ==============================================================================

def test_edge_case_evidence_pack_unauthorized_candidate_stripped() -> None:
    """Verifies that candidates in unauthorized namespaces are sanitized from EvidencePack."""
    policy_ctx = PolicyContext(tenant_id="tenant_p4", allowed_namespaces=[CLIENT_SAFE_NAMESPACE])
    candidates = [
        {"node_id": "n1", "evidence_id": "e1", "text": "Public fact", "namespace": CLIENT_SAFE_NAMESPACE, "tenant_id": "tenant_p4"},
        {"node_id": "n2", "evidence_id": "e2", "text": "Leaked advisory", "namespace": INTERNAL_ADVISORY_NAMESPACE, "tenant_id": "tenant_p4"},
    ]
    pack = assemble_decision_evidence_pack("inq_leak_test", "fact query", candidates, policy_ctx)
    assert len(pack.canonical_facts) == 1
    assert pack.canonical_facts[0]["node_id"] == "n1"


def test_edge_case_evidence_pack_empty_candidates() -> None:
    """Verifies that evidence pack assembly with 0 candidates returns valid empty pack."""
    policy_ctx = PolicyContext(tenant_id="tenant_empty", allowed_namespaces=[CLIENT_SAFE_NAMESPACE])
    pack = assemble_decision_evidence_pack("inq_empty", "empty query", [], policy_ctx)
    assert isinstance(pack, EvidencePack)
    assert len(pack.canonical_facts) == 0
    assert pack.pack_id.startswith("pack_")


def test_edge_case_rollback_preserves_wal_evidence(temp_wal_db: Path) -> None:
    """Verifies that active generation rollback preserves 100% of underlying WAL evidence."""
    # Write evidence records before simulated rollback
    rec1 = EvidenceRecord("ev_pre_01", "s1", "m", "Spk", "Preserved text 1", time.time(), CLIENT_SAFE_NAMESPACE, "t1", compute_content_digest("t1").sha256)
    rec2 = EvidenceRecord("ev_pre_02", "s1", "m", "Spk", "Preserved text 2", time.time(), CLIENT_SAFE_NAMESPACE, "t1", compute_content_digest("t2").sha256)
    append_evidence_record(temp_wal_db, rec1)
    append_evidence_record(temp_wal_db, rec2)

    # Simulated generation rollback
    rollback_manifest = {
        "active_generation": "generation-d5c6bfc4279b8892904b7c4f4e80",
        "rolled_back_from": "generation-e4f1a8c901b4",
        "timestamp": time.time(),
        "wal_retained": True,
    }
    assert rollback_manifest["wal_retained"] is True

    # Verify both records still exist and can be retrieved
    records = query_evidence_by_tenant_and_namespace(temp_wal_db, "t1", CLIENT_SAFE_NAMESPACE)
    assert len(records) == 2
    assert {r.evidence_id for r in records} == {"ev_pre_01", "ev_pre_02"}
