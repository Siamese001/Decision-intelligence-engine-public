"""
tests/test_hitl_upload_publication.py

Unit tests for Wave 5: Active publication verification, read-only graph inspection,
future-run entity resolver caching, temporal validity, and truthful completion gates.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
)
from dual_pipeline.canonical_pipeline.publication_verifier import (
    validate_publication_coverage_receipt,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    _validate_stale_and_digest,
    _validate_status_and_idempotency,
    compute_proposal_digest_from_deliverables,
    mark_mutation_activated,
    validate_mutation_authorization,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    ResolvedEntity,
    invalidate_resolver_cache,
    resolve_person_entity,
)


def _init_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


class TestHITLUploadPublication:
    """Unit tests for publication fact coverage and graph inspection."""

    def test_schema_validation_publication_coverage_receipt(self):
        valid_receipt = {
            "contract_id": "urn:nee:dual-pipeline:publication-coverage-receipt:1.0.0",
            "schema_version": "1.0.0",
            "receipt_id": "pcr-abc123def456",
            "run_id": "run-001",
            "proposal_digest": "a" * 64,
            "activated_generation_id": "gen-001",
            "target_graph": "CANONICAL_GRAPH.db",
            "status": "VERIFIED",
            "covered_facts": [
                {
                    "fact_id": "item-1",
                    "item_type": "SPEAKER_ROLE",
                    "canonical_identity": "Jordan Taylor",
                    "target_property": "HAS_ROLE",
                    "before_value": "Participant",
                    "after_value": "Product Owner",
                    "temporal_scope": "2026-Q4",
                    "authority": "entry-001",
                    "disposition": "COMMITTED",
                    "disposition_reason": "Fact successfully written to canonical graph.",
                    "verified_in_graph": True,
                    "graph_row_id": "node-101",
                }
            ],
            "coverage_summary": {
                "total_eligible_facts": 1,
                "committed_count": 1,
                "existing_noop_count": 0,
                "excluded_count": 0,
                "quarantined_count": 0,
                "missing_count": 0,
            },
            "verified_at": "2026-10-08T12:00:00Z",
        }
        is_val, errs = validate_publication_coverage_receipt(valid_receipt)
        assert is_val, f"Validation failed: {errs}"

    def test_independent_readonly_inspection_confirms_committed_values(self, tmp_path: Path):
        db_p = tmp_path / "test_graph.sqlite"
        conn = _init_test_graph(db_p)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-101', 'Brian Glick', 'Brian Glick', '{\"role\": \"Lead Engineer\", \"org\": \"Apex Global\"}', 'person', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
            "VALUES ('ass-201', 'n-101', 'HAS_ROLE', 'Lead Engineer', 'Brian is Lead Engineer', 'CURRENT')"
        )
        conn.commit()
        conn.close()

        item = EvidenceItem(
            item_id="fact-role-1",
            item_type=ItemType.SPEAKER_ROLE.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.PROPOSED.value,
            source_origin={"source_type": "TRANSCRIPT_HITL", "source_hash": "b" * 64},
            disposition_reason="",
            proposed_fact={"subject": "Brian Glick", "predicate": "HAS_ROLE", "object": "Lead Engineer"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-test",
            run_id="run-101",
            source_digest="c" * 64,
            items=[item],
        )

        base_snapshot = {"nodes": {"brian glick": {"has_role": "Participant"}}}
        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_p,
            manifest=manifest,
            proposal_digest="d" * 64,
            activated_generation_id="gen-002",
            output_dir=tmp_path,
            base_snapshot=base_snapshot,
        )

        assert ok, f"Violations: {violations}"
        assert receipt["status"] == "VERIFIED"
        assert receipt["coverage_summary"]["committed_count"] == 1
        fact = receipt["covered_facts"][0]
        assert fact["disposition"] == "COMMITTED"
        assert fact["before_value"] == "Participant"
        assert fact["after_value"] == "Lead Engineer"
        assert fact["graph_row_id"] == "ass-201"
        assert (tmp_path / "publication_coverage_receipt.json").is_file()

    def test_existing_fact_noop_requires_canonical_identity_value_and_authority(self, tmp_path: Path):
        db_p = tmp_path / "test_graph.sqlite"
        conn = _init_test_graph(db_p)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-102', 'Jordan Taylor', 'Jordan Taylor', '{\"role\": \"Product Owner\"}', 'person', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
            "VALUES ('ass-202', 'n-102', 'HAS_ROLE', 'Product Owner', 'Tenika is Product Owner', 'CURRENT')"
        )
        conn.commit()
        conn.close()

        item = EvidenceItem(
            item_id="fact-role-2",
            item_type=ItemType.SPEAKER_ROLE.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.PROPOSED.value,
            source_origin={"source_type": "TRANSCRIPT_HITL", "source_hash": "b" * 64},
            disposition_reason="",
            proposed_fact={"subject": "Jordan Taylor", "predicate": "HAS_ROLE", "object": "Product Owner"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-test-noop",
            run_id="run-102",
            source_digest="c" * 64,
            items=[item],
        )

        base_snapshot = {"nodes": {"Jordan Taylor": {"has_role": "Product Owner"}}}
        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_p,
            manifest=manifest,
            proposal_digest="e" * 64,
            activated_generation_id="gen-002",
            output_dir=tmp_path,
            base_snapshot=base_snapshot,
        )

        assert ok
        fact = receipt["covered_facts"][0]
        assert fact["disposition"] == "EXISTING_FACT_NOOP"
        assert "matches identity" in fact["disposition_reason"]
        assert receipt["coverage_summary"]["existing_noop_count"] == 1

    def test_missing_fact_in_graph_fails_publication_coverage(self, tmp_path: Path):
        db_p = tmp_path / "empty_graph.sqlite"
        conn = _init_test_graph(db_p)
        conn.close()

        item = EvidenceItem(
            item_id="fact-missing-1",
            item_type=ItemType.SPEAKER_ROLE.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.PROPOSED.value,
            source_origin={"source_type": "TRANSCRIPT_HITL", "source_hash": "b" * 64},
            disposition_reason="",
            proposed_fact={"subject": "Nonexistent Person", "predicate": "HAS_ROLE", "object": "Director"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-missing",
            run_id="run-103",
            source_digest="c" * 64,
            items=[item],
        )

        ok, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=db_p,
            manifest=manifest,
            proposal_digest="f" * 64,
            activated_generation_id="gen-003",
        )
        assert not ok
        assert receipt["status"] == "FAILED"
        assert receipt["coverage_summary"]["missing_count"] == 1
        assert "not found in active graph" in violations[0]

    def test_stale_concrete_base_generation_rejected_even_if_ingress_said_current(self, tmp_path: Path):
        rec = {
            "request_id": "req-001",
            "status": "APPROVED",
            "authorized": True,
            "operator_origin": "human_operator",
            "expected_base_generation": "current",
            "concrete_base_generation": "gen-001",
        }
        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_stale_and_digest(
                rec=rec,
                active_gen="gen-002",
                out_p=tmp_path,
                rt_root=tmp_path,
                written={},
                metadata={"concrete_base_generation": "gen-001"},
            )
        assert "AUTHORIZATION_DENIED_STALE_BASE_GENERATION" in str(exc_info.value)
        assert "gen-001" in str(exc_info.value)
        assert "gen-002" in str(exc_info.value)

    def test_changed_proposal_deliverables_rejected_after_approval(self, tmp_path: Path):
        intake_p = tmp_path / "intake_package.json"
        intake_p.write_text("{\"v\": 1}", encoding="utf-8")
        out1_p = tmp_path / "output_01_transcript.md"
        out1_p.write_text("Original content", encoding="utf-8")
        out2_p = tmp_path / "output_02_recap.md"
        out2_p.write_text("Recap", encoding="utf-8")
        out3_p = tmp_path / "output_03_graph.json"
        out3_p.write_text("{\"nodes\": []}", encoding="utf-8")

        written = {
            "01_transcript": str(out1_p),
            "02_recap": str(out2_p),
            "03_graph": str(out3_p),
            "intake_package": str(intake_p),
        }
        rec = {
            "meeting_name": "Test Meeting",
            "proposal_digest": "f" * 64,
            "expected_base_generation": "gen-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }

        with pytest.raises(MutationAuthorizationError) as exc_info:
            _validate_stale_and_digest(
                rec=rec,
                active_gen="gen-001",
                out_p=tmp_path,
                rt_root=tmp_path,
                written=written,
                metadata={},
            )
        assert "AUTHORIZATION_DENIED_DIGEST_MISMATCH" in str(exc_info.value)

    def test_future_run_reader_with_runtime_binding_and_temporal_filtering(self, tmp_path: Path):
        db_p = tmp_path / "graph.sqlite"
        conn = _init_test_graph(db_p)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-p1', 'Alice Vance', 'Alice Vance', '{\"role\": \"Archived Role\"}', 'person', 1)"
        )
        # Current valid assertion
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
            "VALUES ('ass-curr', 'n-p1', 'HAS_ROLE', 'Principal Architect', 'Alice is Principal Architect', 'CURRENT')"
        )
        # Superseded assertion that should be ignored
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state, superseded_by_assertion_id) "
            "VALUES ('ass-old', 'n-p1', 'HAS_ROLE', 'Senior Architect', 'Alice was Senior Architect', 'SUPERSEDED', 'ass-curr')"
        )
        conn.commit()
        conn.close()

        ap = tmp_path / "active_pair.json"
        ap.write_text(json.dumps({"generation_id": "gen-active-99", "graph_path": "graph.sqlite"}), encoding="utf-8")

        resolver = PreSynthesisEntityResolver(runtime_root=tmp_path)
        ent = resolver.resolve("Alice Vance")
        assert ent is not None
        assert ent.role == "Principal Architect"
        assert ent.generation_id == "gen-active-99"
        assert ent.assertion_id == "ass-curr"
        assert ent.source == "knowledge_graph_assertion"

    def test_resolver_cache_invalidation_and_runtime_isolation(self, tmp_path: Path):
        rt1 = tmp_path / "rt1"
        rt2 = tmp_path / "rt2"
        rt1.mkdir()
        rt2.mkdir()

        # RT1: Bob is "Director"
        db1 = rt1 / "graph.sqlite"
        c1 = _init_test_graph(db1)
        c1.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-b1', 'Bob Miller', 'Bob Miller', '{\"role\": \"Director\"}', 'person', 1)"
        )
        c1.commit()
        c1.close()
        (rt1 / "active_pair.json").write_text(json.dumps({"generation_id": "gen-rt1", "graph_path": "graph.sqlite"}), encoding="utf-8")

        # RT2: Bob is "VP Engineering"
        db2 = rt2 / "graph.sqlite"
        c2 = _init_test_graph(db2)
        c2.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-b2', 'Bob Miller', 'Bob Miller', '{\"role\": \"VP Engineering\"}', 'person', 1)"
        )
        c2.commit()
        c2.close()
        (rt2 / "active_pair.json").write_text(json.dumps({"generation_id": "gen-rt2", "graph_path": "graph.sqlite"}), encoding="utf-8")

        # Scoped resolutions do not leak
        ent1 = resolve_person_entity("Bob Miller", runtime_root=rt1)
        assert ent1 is not None and ent1.role == "Director"
        assert ent1.generation_id == "gen-rt1"

        ent2 = resolve_person_entity("Bob Miller", runtime_root=rt2)
        assert ent2 is not None and ent2.role == "VP Engineering"
        assert ent2.generation_id == "gen-rt2"

        # Explicit cache invalidation
        invalidate_resolver_cache()
        ent1_reloaded = resolve_person_entity("Bob Miller", runtime_root=rt1)
        assert ent1_reloaded is not None and ent1_reloaded.role == "Director"
