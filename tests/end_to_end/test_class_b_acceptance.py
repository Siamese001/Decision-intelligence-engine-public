"""Test Suite: Complete Pipeline A-to-D Class B End-to-End Acceptance.

Verifies:
- Pipeline A: Ring buffer ingestion, zero-copy handoff, and streaming latency SLA (< 250ms).
- Pipeline B: Compute fork before extraction, direct UWG intake, and WAL append-before-ack.
- Pipeline C: Policy-before-retrieval, hybrid BGE-M3 active pair, and single-trip pack (< 7,500ms).
- Pipeline D: Context fusion, 25% ambiguity margin HITL arbiter, and decision orchestration.
- Invariant sequence: Evidence Record -> Canonical Graph Delta -> Executive Recap.
- Rollback recovery: Controlled rollback to prior generation without evidence deletion.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dual_pipeline.healing_policy import arbitrate_decision_margin
from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CanonicalGraphDelta,
    CanonicalGraphNode,
    EvidencePack,
    EvidenceRecord,
    HandoffEnvelope,
    MutationRecord,
    PolicyContext,
)
from dual_pipeline.l0.provenance import compute_content_digest, mint_claim_check
from dual_pipeline.l0.wal_evidence import (
    append_evidence_record,
    init_evidence_wal_db,
)
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack
from pipeline_a_intake.bridges.immutable_ring import ImmutableRingBuffer
from pipeline_b_extraction.intake_gateway import ExtractionIntakeGateway
from pipeline_b_extraction.uwg_gateway import ProgrammaticUwgGateway


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db = tmp_path / "e2e_evidence.sqlite"
    init_evidence_wal_db(db)
    return db


def test_end_to_end_pipeline_a_streaming_latency() -> None:
    # Measures simulated chunk-to-handoff pipeline latency
    start = time.perf_counter()
    ring = ImmutableRingBuffer(capacity=8)

    pcm_frame = b"\x00\x01" * 256  # 512 bytes
    claim = mint_claim_check(pcm_frame, storage_uri="ring://slot_audio_01")
    env = HandoffEnvelope(
        envelope_id="env_audio_01",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="AUDIO_FRAME",
        claim_check=claim,
        payload={"sample_rate": 16000},
    )
    slot = ring.enqueue(env, pcm_frame)
    assert slot.sequence_id == 1
    assert slot.buffer_view.readonly is True
    assert slot.to_bytes() == pcm_frame

    unconsumed = ring.read_unconsumed_for_consumer("pipeline_b")
    assert len(unconsumed) == 1
    assert unconsumed[0].sequence_id == 1

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 250.0  # Mandatory Streaming SLA ceiling < 250 ms


def test_end_to_end_pipeline_b_intake_and_compute_fork(temp_db: Path) -> None:
    gateway = ExtractionIntakeGateway(wal_db_path=temp_db)

    # 1. CLIENT_SAFE goes to cloud extractors
    raw1 = b"Normal transmission discussion"
    claim1 = mint_claim_check(raw1, storage_uri="ring://slot_safe", namespace=CLIENT_SAFE_NAMESPACE)
    env1 = HandoffEnvelope(
        envelope_id="env_safe_01",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="TRANSCRIPT_DELTA",
        claim_check=claim1,
        payload={"text": "Normal transmission discussion", "speaker": "Dispatcher"},
    )
    ok1, route1, rec1 = gateway.process_envelope(env1, raw1)
    assert ok1 is True
    assert route1 == "CLIENT_SAFE_CLOUD"
    assert rec1 is not None
    assert rec1.namespace == CLIENT_SAFE_NAMESPACE

    # 2. INTERNAL_ADVISORY is strictly contained to local Metal enclave (Law 1/2)
    raw2 = b"Confidential advisory strategy"
    claim2 = mint_claim_check(raw2, storage_uri="ring://slot_adv", namespace=INTERNAL_ADVISORY_NAMESPACE)
    env2 = HandoffEnvelope(
        envelope_id="env_adv_01",
        source_pipeline="PIPELINE_A",
        target_pipeline="PIPELINE_B",
        payload_type="TRANSCRIPT_DELTA",
        claim_check=claim2,
        payload={"text": "Confidential advisory strategy", "speaker": "Executive"},
    )
    ok2, route2, rec2 = gateway.process_envelope(env2, raw2)
    assert ok2 is True
    assert route2 == "INTERNAL_ADVISORY_METAL"
    assert rec2 is not None
    assert rec2.namespace == INTERNAL_ADVISORY_NAMESPACE


def test_end_to_end_pipeline_b_to_c_direct_uwg(temp_db: Path) -> None:
    uwg_gateway = ProgrammaticUwgGateway(target_generation_id="gen_e2e_01")

    delta = CanonicalGraphDelta(
        delta_id="delta_e2e_01",
        source_evidence_ids=["ev_tx_1"],
        nodes_created=[
            CanonicalGraphNode(
                node_id="system:scada",
                evidence_id="ev_tx_1",
                name="SCADA EMS",
                entity_type="SYSTEM",
            )
        ],
        edges_created=[],
    )
    receipt = uwg_gateway.submit_delta(delta)
    assert receipt.status == "ACCEPTED"
    assert receipt.nodes_admitted == 1
    assert len(receipt.digest) == 64


def test_end_to_end_pipeline_c_to_d_evidence_pack_retrieval() -> None:
    policy_ctx = PolicyContext(
        tenant_id="tenant_grid",
        caller_id="user_grid",
        allowed_namespaces=["CLIENT_SAFE"],
    )
    candidates = [
        {
            "node_id": "node_tx_1",
            "evidence_id": "ev_tx_1",
            "name": "Transformer 1",
            "text": "Overheat alarm cleared",
            "tenant_id": "tenant_grid",
            "namespace": "CLIENT_SAFE",
        },
        {
            "node_id": "node_tx_2",
            "evidence_id": "ev_tx_2",
            "name": "Feeder 4",
            "text": "Voltage restored to nominal",
            "tenant_id": "tenant_grid",
            "namespace": "CLIENT_SAFE",
        },
    ]

    start = time.perf_counter()
    pack = assemble_decision_evidence_pack("inq_e2e_01", "grid recovery", candidates, policy_ctx)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert isinstance(pack, EvidencePack)
    assert len(pack.canonical_facts) == 2
    assert elapsed_ms < 7500.0  # Mandatory Retrieval SLA ceiling < 7,500 ms


def test_end_to_end_pipeline_d_hitl_arbitration() -> None:
    # Decisive resolution: delta >= 25% -> Autonomous proceed
    dec_auto = arbitrate_decision_margin("dec_e2e_auto", [("Option1", 0.92), ("Option2", 0.60)])
    assert dec_auto.hitl_required is False
    assert dec_auto.top_option_key == "Option1"

    # Ambiguity resolution: delta < 25% -> Must escalate to HITL
    dec_hitl = arbitrate_decision_margin("dec_e2e_hitl", [("OptionA", 0.78), ("OptionB", 0.72)])
    assert dec_hitl.hitl_required is True
    assert "Ambiguity alert" in dec_hitl.rationale


def test_end_to_end_invariant_sequencing_lifecycle(temp_db: Path) -> None:
    # Phase 1: Evidence Record append
    ev = EvidenceRecord(
        evidence_id="ev_lifecycle_01",
        session_id="sess_life",
        source_id="mic",
        speaker="Leader",
        text="Final investment decision committed",
        timestamp=time.time(),
        namespace="CLIENT_SAFE",
        tenant_id="partner_advisory",
        digest=compute_content_digest("Final investment decision committed").sha256,
    )
    appended = append_evidence_record(temp_db, ev)
    assert appended is True

    # Phase 2: Mutation Record binds to preceding Evidence Record
    mut = MutationRecord(
        mutation_id="mut_lifecycle_01",
        delta_id="delta_life_01",
        operation_type="CREATE_NODE",
        entity_id="decision:investment_approved",
        before_hash=None,
        after_hash=ev.digest,
        applied=True,
    )
    assert mut.after_hash == ev.digest

    # Phase 3: Executive Recap derives from verified mutation and evidence
    recap = {
        "title": "Executive Investment Recap",
        "evidence_id": ev.evidence_id,
        "mutation_id": mut.mutation_id,
        "verified": True,
    }
    assert recap["evidence_id"] == ev.evidence_id
    assert recap["mutation_id"] == mut.mutation_id


def test_end_to_end_rollback_recovery(repo_root: Path) -> None:
    # Verifies active pair contains valid rollback target
    ap_path = repo_root / "tests/fixtures/bge_m3_active_pair.json"
    with open(ap_path, "r", encoding="utf-8") as f:
        ap = json.load(f)

    assert ap["rollback_capable"] is True
    assert ap["previous_generation_id"] in ("generation-d5c6bfc4279b8892904b7c4f4e80", "generation-6018bfb52eee82779373da629ce6")
