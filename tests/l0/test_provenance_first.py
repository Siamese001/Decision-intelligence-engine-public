"""Test suite for Law 4 (Immutable Provenance First), Claim-Checks & WAL storage."""
from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.l0.contracts import EvidenceRecord
from dual_pipeline.l0.provenance import (
    canonical_json_bytes,
    chain_parent_digests,
    compute_content_digest,
    mint_claim_check,
    verify_digest_integrity,
)
from dual_pipeline.l0.wal_evidence import (
    append_evidence_record,
    get_evidence_record_by_digest,
    init_evidence_wal_db,
)


def test_rfc8259_canonical_bytes_key_sorting():
    """Verifies that dict key order does not alter the canonical SHA-256 digest."""
    d1 = {"z_key": 1, "a_key": 2, "m_key": {"sub_b": "hello", "sub_a": "world"}}
    d2 = {"a_key": 2, "m_key": {"sub_a": "world", "sub_b": "hello"}, "z_key": 1}

    bytes1 = canonical_json_bytes(d1)
    bytes2 = canonical_json_bytes(d2)
    assert bytes1 == bytes2
    assert compute_content_digest(d1).sha256 == compute_content_digest(d2).sha256


def test_claim_check_minting():
    """Verifies Claim-Check minting and integrity verification."""
    payload = {"turn_id": "turn_123", "text": "Confirmed architectural milestone."}
    claim = mint_claim_check(payload, storage_uri="sqlite:///data/evidence.db")
    assert claim.digest != ""
    assert claim.claim_id.startswith("claim:client_safe:")
    assert verify_digest_integrity(payload, claim.digest)


def test_parent_digest_chaining():
    """Verifies deterministic parent digest chaining."""
    parents = ["hash_b_2222", "hash_a_1111", "hash_c_3333"]
    chained1 = chain_parent_digests(parents)
    chained2 = chain_parent_digests(["hash_c_3333", "hash_a_1111", "hash_b_2222"])
    assert chained1 == chained2


def test_wal_evidence_storage_idempotency():
    """Verifies that SQLite WAL append operations are atomic and idempotent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_evidence.db"
        init_evidence_wal_db(db_path)

        rec = EvidenceRecord(
            evidence_id="ev_test_101",
            session_id="sess_101",
            source_id="src_101",
            speaker="Andrew Hanson",
            text="Tanner Stevens confirmed evaluation framework.",
            timestamp=1700000000.0,
            namespace="CLIENT_SAFE",
            tenant_id="partner_advisory",
        )

        # First append succeeds
        inserted1 = append_evidence_record(db_path, rec)
        assert inserted1 is True

        # Second append is idempotent duplicate (returns False, does not crash)
        inserted2 = append_evidence_record(db_path, rec)
        assert inserted2 is False

        # Retrieval by digest matches
        retrieved = get_evidence_record_by_digest(db_path, rec.digest)
        assert retrieved is not None
        assert retrieved.evidence_id == "ev_test_101"
        assert retrieved.speaker == "Andrew Hanson"
