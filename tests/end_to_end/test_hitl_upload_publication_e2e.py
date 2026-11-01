"""
tests/end_to_end/test_hitl_upload_publication_e2e.py

End-to-End tests for Wave 5: Full publication flow with factual coverage readback,
package-only truthful non-activation, and idempotent publication recovery.
"""
from __future__ import annotations

import json
import sqlite3
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
    record_publication_fact_coverage,
    validate_publication_coverage_receipt,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _handle_publication_activation,
    _resolve_pipeline_status,
    publish_graph_if_requested,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    mark_mutation_activated,
    validate_mutation_authorization,
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


class TestHITLUploadPublicationE2E:
    """E2E verification of graph publication coverage and truthful lifecycle gates."""

    def test_e2e_full_publication_with_fact_coverage_receipt(self, tmp_path: Path):
        out_p = tmp_path / "output_test"
        out_p.mkdir(parents=True, exist_ok=True)
        db_p = out_p / "CANONICAL_GRAPH.db"
        conn = _init_test_graph(db_p)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, display_name, properties_json, node_type, active) "
            "VALUES ('n-101', 'Jordan Taylor', 'Jordan Taylor', '{\"role\": \"Lead PO\", \"org\": \"PartnerAdvisory\"}', 'person', 1)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, claim_text, currentness_state) "
            "VALUES ('ass-301', 'n-101', 'HAS_ROLE', 'Lead PO', 'Jordan Taylor is Lead PO', 'CURRENT')"
        )
        conn.commit()
        conn.close()

        item_role = EvidenceItem(
            item_id="ev-role-1",
            item_type=ItemType.SPEAKER_ROLE.value,
            is_eligible_graph_fact=True,
            state=EvidenceState.PROPOSED.value,
            source_origin={"source_type": "TRANSCRIPT_HITL", "source_hash": "a" * 64},
            disposition_reason="Confirmed in meeting intake",
            proposed_fact={"subject": "Jordan Taylor", "predicate": "HAS_ROLE", "object": "Lead PO"},
            approval_ref={"entry_id": "appr-001", "status": "RESOLVED_VALID"},
        )
        manifest = EvidenceCoverageManifest(
            manifest_id="cov-e2e-1",
            run_id="run-e2e-1",
            source_digest="b" * 64,
            items=[item_role],
        )

        metadata = {
            "meeting_name": "E2E Intake Meeting",
            "runtime_root": str(out_p),
            "target_graph": str(db_p),
            "evidence_manifest": manifest,
            "proposal_digest": "c" * 64,
        }
        auth_info = {
            "request_id": "req-e2e-1",
            "proposal_digest": "c" * 64,
            "authorized": True,
            "status": "APPROVED",
        }
        pub_res = {
            "status": "COMPLETE",
            "generation_id": "gen-e2e-100",
            "target_graph": str(db_p),
        }
        written: Dict[str, str] = {}

        res = _handle_publication_activation(pub_res, written, auth_info, metadata, out_p)
        assert res is not None
        assert "publication_coverage_receipt" in res

        receipt_file = out_p / "publication_coverage_receipt.json"
        assert receipt_file.is_file()

        pcr = json.loads(receipt_file.read_text(encoding="utf-8"))
        is_val, errs = validate_publication_coverage_receipt(pcr)
        assert is_val, f"PCR invalid: {errs}"
        assert pcr["status"] == "VERIFIED"
        assert pcr["coverage_summary"]["committed_count"] == 1
        assert pcr["covered_facts"][0]["disposition"] == "COMMITTED"
        assert pcr["covered_facts"][0]["after_value"] == "Lead PO"

    def test_e2e_package_only_never_claims_activation(self, tmp_path: Path):
        metadata = {
            "package_only": True,
            "mutate_graph": True,
            "auto_mutate_on_pass": True,
            "meeting_name": "Package Only Run",
        }
        status = _resolve_pipeline_status(None, metadata)
        assert status == "PACKAGE_SEALED"

        pkg_path = tmp_path / "intake_package.json"
        pkg_path.write_text("{}", encoding="utf-8")
        pub_out = publish_graph_if_requested(
            mutate_graph=False,
            intake_pkg_path=pkg_path,
            metadata=metadata,
            written={},
            directive_id=None,
            reviewer=None,
            rationale=None,
            embedding_backend="mock",
        )
        assert pub_out is None

    def test_e2e_idempotent_publication_recovery(self, tmp_path: Path):
        rt_root = tmp_path / "rt_idem"
        rt_root.mkdir()
        ap_file = rt_root / "active_pair.json"
        ap_file.write_text(json.dumps({"generation_id": "gen-active-500", "graph_path": "graph.sqlite"}), encoding="utf-8")

        approval_store = rt_root / "approval"
        approval_store.mkdir()
        requests_file = approval_store / "host_approval_requests.json"
        req_data = {
            "req-idem-1": {
                "request_id": "req-idem-1",
                "meeting_name": "Idempotent Meeting",
                "proposal_digest": "d" * 64,
                "status": "ACTIVATED",
                "authorized": True,
                "activated_generation_id": "gen-active-500",
                "operator_origin": "human_operator",
            }
        }
        requests_file.write_text(json.dumps(req_data), encoding="utf-8")

        auth_res = validate_mutation_authorization(
            output_dir=rt_root,
            metadata={"meeting_name": "Idempotent Meeting", "approval_request_id": "req-idem-1"},
            written={},
            runtime_root=rt_root,
            allow_idempotent_recovery=True,
        )
        assert auth_res["authorized"] is True
        assert auth_res["status"] == "ACTIVATED"
        assert auth_res["idempotent"] is True
        assert auth_res["activated_generation_id"] == "gen-active-500"
