"""Test suite for Law 1 (Compute Fork BEFORE Extraction & WAL append-before-ack)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    ClaimCheck,
    HandoffEnvelope,
)
from dual_pipeline.l0.provenance import mint_claim_check
from pipeline_b_extraction.intake_gateway import ExtractionIntakeGateway


def test_compute_fork_routing_client_safe_vs_advisory():
    """Verifies that envelope classification determines compute enclave before extraction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_db = Path(tmpdir) / "evidence.db"
        gateway = ExtractionIntakeGateway(wal_db_path=wal_db)

        # 1. CLIENT_SAFE envelope -> CLIENT_SAFE_CLOUD
        raw1 = b"Public architecture text."
        claim1 = mint_claim_check(raw1, storage_uri="ring://slot1", namespace=CLIENT_SAFE_NAMESPACE)
        env1 = HandoffEnvelope(
            envelope_id="env_cs",
            source_pipeline="PIPELINE_A",
            target_pipeline="PIPELINE_B",
            payload_type="TRANSCRIPT_DELTA",
            claim_check=claim1,
            payload={"text": "Public architecture text.", "speaker": "Speaker"},
        )
        ok1, route1, rec1 = gateway.process_envelope(env1, raw1)
        assert ok1 is True
        assert route1 == "CLIENT_SAFE_CLOUD"
        assert rec1 is not None
        assert rec1.namespace == CLIENT_SAFE_NAMESPACE

        # 2. INTERNAL_ADVISORY envelope -> INTERNAL_ADVISORY_METAL
        raw2 = b"Internal dynamics note."
        claim2 = mint_claim_check(raw2, storage_uri="ring://slot2", namespace=INTERNAL_ADVISORY_NAMESPACE)
        env2 = HandoffEnvelope(
            envelope_id="env_ia",
            source_pipeline="PIPELINE_A",
            target_pipeline="PIPELINE_B",
            payload_type="TRANSCRIPT_DELTA",
            claim_check=claim2,
            payload={"text": "Internal dynamics note.", "speaker": "Speaker"},
        )
        ok2, route2, rec2 = gateway.process_envelope(env2, raw2)
        assert ok2 is True
        assert route2 == "INTERNAL_ADVISORY_METAL"
        assert rec2 is not None
        assert rec2.namespace == INTERNAL_ADVISORY_NAMESPACE


def test_compute_fork_unclassified_rejected():
    """Verifies that unclassified namespace fails closed before extraction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_db = Path(tmpdir) / "evidence.db"
        gateway = ExtractionIntakeGateway(wal_db_path=wal_db)

        claim = ClaimCheck(
            claim_id="claim_unknown",
            digest="d1d2d3",
            storage_uri="ring://slot3",
            namespace="UNKNOWN_NAMESPACE",  # Illegal!
        )
        env = HandoffEnvelope(
            envelope_id="env_bad",
            source_pipeline="PIPELINE_A",
            target_pipeline="PIPELINE_B",
            payload_type="TRANSCRIPT_DELTA",
            claim_check=claim,
            payload={"text": "Unclassified text."},
        )

        ok, route, _ = gateway.process_envelope(env)
        assert ok is False
        assert route == "ROUTING_FAILED"
