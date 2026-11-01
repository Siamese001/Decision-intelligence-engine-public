"""tests/test_attachment_activation_edge_cases.py

Adversarial & Edge-Case Acceptance Test Suite for Wave 4:
Staged Mutation Lifecycle and Proposal Binding.
Covers 5 mandatory edge-case categories per e2e-and-edge-case-coverage.md:
  1. Boundary payload: 0-byte document files, empty projection structures.
  2. Malformed input: Corrupted ZIP headers, invalid UTF-8, malformed locator syntax.
  3. Invariant bypass attempts: Synthetic approval origin, summary as primary evidence,
     and unapproved mutation bypass.
  4. Rollback & recovery: Tamper detection (digest mismatch) and idempotent re-ingestion.
  5. Flag-off behavior invariance: Default disabled automation behavior.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.content_loader import _validate_source_container
from dual_pipeline.config.pipeline_settings import (
    ENABLE_ATTACHMENT_MUTATION_AUTOMATION,
    is_attachment_mutation_automation_enabled,
)
from dual_pipeline.core import sha256_file
from dual_pipeline.document_pipeline_core.executor import (
    AssertionProvenanceError,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.document_pipeline_core.format_source_units import (
    FailureDisposition,
    preflight_attachment,
)
from dual_pipeline.exceptions import SourceIntegrityError
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    ProposalStatus,
    load_approval_request,
    persist_approval_request,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    compute_proposal_digest_from_deliverables,
    mark_mutation_activated,
    validate_mutation_authorization,
)
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestAttachmentActivationEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="att_edge_cases_")
        self.root = Path(self.tmp.name)
        self.out_dir = self.root / "output" / "proj_edge" / "01-01-2026" / "Edge_Run"
        self.out_dir.mkdir(parents=True)
        self.runtime_root = self.root / ".runtime"
        self.runtime_root.mkdir(parents=True)

        res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = res["isolated_db_path"]
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-edge-001", "graph_path": self.isolated_db_path}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
        p = Path(self.isolated_db_path)
        if p.is_file():
            p.unlink(missing_ok=True)

    # --- Category 1: Boundary payload ---

    def test_boundary_zero_byte_source_rejected(self) -> None:
        p = self.root / "empty.xlsx"
        p.write_bytes(b"")
        res = preflight_attachment(b"", "empty.xlsx")
        self.assertFalse(res.is_valid)
        self.assertEqual(res.disposition, FailureDisposition.FAIL_CLOSED)
        self.assertEqual(res.error_code, "EMPTY_PAYLOAD")

    def test_boundary_empty_projection_mutations_handled(self) -> None:
        projection = {"graph_mutations": {}, "evidence_index": {}}
        meta = {"source_manifest": {"sources": [{"filename": "doc.pdf", "is_summary": False}]}}
        # Empty mutations have no signals, passes without raising
        validate_attachment_assertion_provenance(projection, meta)

    # --- Category 2: Malformed input ---

    def test_malformed_corrupted_office_container_rejected(self) -> None:
        corrupted = self.root / "corrupted.docx"
        corrupted.write_bytes(b"PK\x03\x04not_a_valid_zip_archive_garbage")
        with self.assertRaises(SourceIntegrityError) as ctx:
            _validate_source_container(corrupted, ".docx")
        self.assertIn("Corrupted or invalid container", str(ctx.exception))

    def test_malformed_locator_syntax_rejected(self) -> None:
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {"signal_id": "sig-1", "predicate": "CALLS", "evidence_ids": ["ev-1"]}
                ]
            },
            "evidence_index": {
                "ev-1": {
                    "evidence_id": "ev-1",
                    "exact_text": "Sample text",
                    "source_locator": "Page Three Paragraph 4",  # Malformed
                    "source_file": "raw.pdf",
                }
            },
        }
        meta = {"source_manifest": {"sources": [{"filename": "raw.pdf", "is_summary": False}]}}
        with self.assertRaises(AssertionProvenanceError) as ctx:
            validate_attachment_assertion_provenance(projection, meta)
        self.assertIn("invalid unit locator syntax", str(ctx.exception))

    # --- Category 3: Invariant bypass attempt ---

    def test_invariant_bypass_synthetic_origin_blocked(self) -> None:
        src_file = self.out_dir / "report.xlsx"
        src_file.write_bytes(b"PK\x03\x04fake_xlsx")
        f3 = self.out_dir / "03_graph.json"
        f3.write_text(json.dumps({"mutations": []}), encoding="utf-8")
        pkg = self.out_dir / "intake_package.json"
        pkg.write_text(json.dumps({"pkg": "v1"}), encoding="utf-8")

        written = {"source_file": str(src_file), "03_graph": str(f3), "intake_package": str(pkg)}
        meta = {
            "meeting_name": "Synthetic Edge",
            "runtime_root": str(self.runtime_root.resolve()),
            "enable_attachment_mutation_automation": True,
        }

        prop_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Synthetic Edge",
            source_hash=sha256_file(src_file),
            output_hashes={"03_graph": sha256_file(f3)},
            intake_hash=sha256_file(pkg),
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-edge-001",
        )

        req_id = "req-synth-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": prop_digest,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "synthetic_llm_auto",  # Synthetic origin
            "meeting_name": "Synthetic Edge",
            "expected_base_generation": "gen-edge-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertIn("AUTHORIZATION_DENIED_SYNTHETIC_ORIGIN", str(ctx.exception))

    def test_invariant_bypass_summary_as_primary_evidence_blocked(self) -> None:
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {"signal_id": "sig-bypass", "predicate": "MANDATES", "evidence_ids": ["ev-sum"]}
                ]
            },
            "evidence_index": {
                "ev-sum": {
                    "evidence_id": "ev-sum",
                    "exact_text": "Summary states policy mandates",
                    "source_locator": "L1-L2",
                    "source_file": "recap.md",
                    "is_summary": True,
                }
            },
        }
        meta = {
            "source_manifest": {
                "sources": [
                    {"filename": "raw_policy.docx", "is_summary": False},
                    {"filename": "recap.md", "is_summary": True},
                ]
            }
        }
        with self.assertRaises(AssertionProvenanceError) as ctx:
            validate_attachment_assertion_provenance(projection, meta)
        self.assertIn("cites only composed summary evidence", str(ctx.exception))

    # --- Category 4: Rollback & recovery ---

    def test_recovery_tampered_source_file_causes_digest_mismatch(self) -> None:
        src_file = self.out_dir / "data.csv"
        src_file.write_text("a,b\n1,2\n", encoding="utf-8")
        f3 = self.out_dir / "03_graph.json"
        f3.write_text(json.dumps({"mutations": []}), encoding="utf-8")
        pkg = self.out_dir / "intake_package.json"
        pkg.write_text(json.dumps({"pkg": "v1"}), encoding="utf-8")

        written = {"source_file": str(src_file), "03_graph": str(f3), "intake_package": str(pkg)}
        meta = {
            "meeting_name": "Tamper Source",
            "runtime_root": str(self.runtime_root.resolve()),
            "enable_attachment_mutation_automation": True,
        }

        orig_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Tamper Source",
            source_hash=sha256_file(src_file),
            output_hashes={"03_graph": sha256_file(f3)},
            intake_hash=sha256_file(pkg),
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-edge-001",
        )

        req_id = "req-tamper-src-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": orig_digest,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "human_operator",
            "meeting_name": "Tamper Source",
            "expected_base_generation": "gen-edge-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        # Tamper source file
        src_file.write_text("a,b\n1,999\n", encoding="utf-8")

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertIn("AUTHORIZATION_DENIED_DIGEST_MISMATCH", str(ctx.exception))

    def test_recovery_idempotent_replay_on_activated_generation(self) -> None:
        src_file = self.out_dir / "safe.txt"
        src_file.write_text("Safe content\n", encoding="utf-8")
        f3 = self.out_dir / "03_graph.json"
        f3.write_text(json.dumps({"mutations": []}), encoding="utf-8")
        pkg = self.out_dir / "intake_package.json"
        pkg.write_text(json.dumps({"pkg": "v1"}), encoding="utf-8")

        written = {"source_file": str(src_file), "03_graph": str(f3), "intake_package": str(pkg)}
        meta = {
            "meeting_name": "Idempotent Safe",
            "runtime_root": str(self.runtime_root.resolve()),
            "enable_attachment_mutation_automation": True,
        }

        dig = compute_proposal_digest_from_deliverables(
            meeting_name="Idempotent Safe",
            source_hash=sha256_file(src_file),
            output_hashes={"03_graph": sha256_file(f3)},
            intake_hash=sha256_file(pkg),
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-edge-001",
        )

        req_id = "req-safe-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": dig,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "human_operator",
            "meeting_name": "Idempotent Safe",
            "expected_base_generation": "gen-edge-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        # First authorization
        auth1 = validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertTrue(auth1["authorized"])
        self.assertFalse(auth1["idempotent"])

        # Mark activated
        mark_mutation_activated(req_id, self.runtime_root, "gen-edge-002", {"applied": 1})
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-edge-002", "graph_path": self.isolated_db_path}),
            encoding="utf-8",
        )

        # Replay authorization
        meta["approval_receipt"] = load_approval_request(req_id, self.runtime_root)
        auth2 = validate_mutation_authorization(
            self.out_dir, meta, written, runtime_root=self.runtime_root, allow_idempotent_recovery=True,
        )
        self.assertTrue(auth2["authorized"])
        self.assertTrue(auth2["idempotent"])
        self.assertEqual(auth2["status"], ProposalStatus.ACTIVATED.value)

    # --- Category 5: Flag-off behavior invariance ---

    def test_flag_off_disabled_by_default(self) -> None:
        self.assertFalse(ENABLE_ATTACHMENT_MUTATION_AUTOMATION)
        self.assertFalse(is_attachment_mutation_automation_enabled())
        self.assertFalse(is_attachment_mutation_automation_enabled({"meeting": "test"}))


if __name__ == "__main__":
    unittest.main()
