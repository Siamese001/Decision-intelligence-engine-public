"""tests/test_attachment_activation.py

Wave 4: Staged Mutation Lifecycle and Proposal Binding for Attachments.
Verifies:
1. Feature flag default (disabled by default) and metadata override.
2. Deliverable staging for source_manifest.json.
3. Assertion provenance validation: exact unit locators and summary exclusion.
4. Proposal generation and deterministic digest calculation.
5. Isolated test DB provisioning and proposal-bound mutation authorization.
6. Idempotent replay and recovery for activated proposals.
7. Tamper detection and fail-closed digest mismatch.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.config.pipeline_settings import (
    ENABLE_ATTACHMENT_MUTATION_AUTOMATION,
    is_attachment_mutation_automation_enabled,
)
from dual_pipeline.document_pipeline_core.executor import (
    AssertionProvenanceError,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    MutationProposal,
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


class TestAttachmentActivation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="test_att_act_")
        self.root = Path(self.tmp.name)
        self.out_dir = self.root / "output" / "proj" / "01-01-2026" / "Doc_Run"
        self.out_dir.mkdir(parents=True)
        self.runtime_root = self.root / ".runtime"
        self.runtime_root.mkdir(parents=True)

        self.active_pair = {
            "generation_id": "gen-test-001",
            "graph_path": "graph.sqlite",
            "sidecar_path": "sidecar.sqlite",
        }
        (self.runtime_root / "active_pair.json").write_text(json.dumps(self.active_pair), encoding="utf-8")

        # Provision isolated DB
        res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = res["isolated_db_path"]

    def tearDown(self) -> None:
        self.tmp.cleanup()
        p = Path(self.isolated_db_path)
        if p.is_file():
            p.unlink(missing_ok=True)

    def test_feature_flag_disabled_by_default(self) -> None:
        self.assertFalse(ENABLE_ATTACHMENT_MUTATION_AUTOMATION)
        self.assertFalse(is_attachment_mutation_automation_enabled())
        self.assertFalse(is_attachment_mutation_automation_enabled({}))
        self.assertTrue(is_attachment_mutation_automation_enabled({"enable_attachment_mutation_automation": True}))
        self.assertFalse(is_attachment_mutation_automation_enabled({"enable_attachment_mutation_automation": False}))

    def test_deploy_deliverables_stages_source_manifest(self) -> None:
        meta = {
            "meeting_name": "Doc Intake",
            "date": "01-01-2026",
            "source_manifest": {
                "schema_version": "urn:nee:dual-pipeline:source-manifest:1.0.0",
                "total_sources": 2,
                "sources": [{"filename": "sample.xlsx", "is_summary": False}],
            },
        }
        synthesis = SynthesisResult(
            output_1="",
            output_2="",
            output_3=json.dumps({"graph_mutations": {}, "evidence_index": {}}),
        )
        artifacts = deploy_deliverables(self.out_dir, meta, CanonicalModality.DOCUMENT, synthesis)
        self.assertIn("source_manifest", artifacts)
        sm_path = Path(artifacts["source_manifest"])
        self.assertTrue(sm_path.is_file())
        data = json.loads(sm_path.read_text(encoding="utf-8"))
        self.assertEqual(data["total_sources"], 2)

    def test_assertion_provenance_accepts_valid_locators(self) -> None:
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-xlsx",
                        "predicate": "ALLOCATES_BUDGET",
                        "evidence_ids": ["ev-xlsx"],
                    },
                    {
                        "signal_id": "sig-pdf",
                        "predicate": "DEFINES_SCOPE",
                        "evidence_ids": ["ev-pdf"],
                    },
                ],
            },
            "evidence_index": {
                "ev-xlsx": {
                    "evidence_id": "ev-xlsx",
                    "exact_text": "Q1 allocation is $50k",
                    "source_locator": "Financials!R2C4",
                    "source_file": "financials.xlsx",
                },
                "ev-pdf": {
                    "evidence_id": "ev-pdf",
                    "exact_text": "Delivery expected Q2",
                    "source_locator": "p2#r1",
                    "source_file": "sow.pdf",
                },
            },
        }
        meta = {
            "source_manifest": {
                "sources": [
                    {"filename": "financials.xlsx", "is_summary": False},
                    {"filename": "sow.pdf", "is_summary": False},
                ]
            }
        }
        # Must not raise
        validate_attachment_assertion_provenance(projection, meta)

    def test_assertion_provenance_rejects_summary_as_primary_evidence(self) -> None:
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-invalid",
                        "predicate": "PROJECTS_REVENUE",
                        "evidence_ids": ["ev-summary"],
                    }
                ],
            },
            "evidence_index": {
                "ev-summary": {
                    "evidence_id": "ev-summary",
                    "exact_text": "Recap summary states revenue up 10%",
                    "source_locator": "L1-L5",
                    "source_file": "executive_summary.md",
                    "is_summary": True,
                }
            },
        }
        meta = {
            "source_manifest": {
                "sources": [
                    {"filename": "raw_ledger.xlsx", "is_summary": False},
                    {"filename": "executive_summary.md", "is_summary": True},
                ]
            }
        }
        with self.assertRaises(AssertionProvenanceError) as ctx:
            validate_attachment_assertion_provenance(projection, meta)
        self.assertIn("cites only composed summary evidence", str(ctx.exception))

    def test_assertion_provenance_rejects_malformed_locator_syntax(self) -> None:
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-bad-loc",
                        "predicate": "OWNS_SYSTEM",
                        "evidence_ids": ["ev-bad"],
                    }
                ],
            },
            "evidence_index": {
                "ev-bad": {
                    "evidence_id": "ev-bad",
                    "exact_text": "System ownership note",
                    "source_locator": "somewhere near bottom of page",
                    "source_file": "raw.pdf",
                }
            },
        }
        meta = {
            "source_manifest": {
                "sources": [{"filename": "raw.pdf", "is_summary": False}]
            }
        }
        with self.assertRaises(AssertionProvenanceError) as ctx:
            validate_attachment_assertion_provenance(projection, meta)
        self.assertIn("invalid unit locator syntax", str(ctx.exception))

    def test_proposal_creation_and_isolated_mutation_flow(self) -> None:
        # Create deliverable files
        src_file = self.out_dir / "report.xlsx"
        src_file.write_bytes(b"PK\x03\x04fake_xlsx_bytes")
        f3 = self.out_dir / "03_graph.json"
        f3.write_text(json.dumps({"mutations": [{"node": "SystemA"}]}), encoding="utf-8")
        pkg = self.out_dir / "intake_package.json"
        pkg.write_text(json.dumps({"meeting": {"meeting_id": "doc-run", "source_hash": "a" * 64}}), encoding="utf-8")

        written = {
            "source_file": str(src_file),
            "03_graph": str(f3),
            "intake_package": str(pkg),
        }
        meta = {
            "meeting_name": "Doc Run",
            "runtime_root": str(self.runtime_root),
            "graph_path": self.isolated_db_path,
            "enable_attachment_mutation_automation": True,
        }

        # Step 1: Unapproved mutation fails closed
        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertIn("AUTHORIZATION_DENIED_MISSING_APPROVAL", str(ctx.exception))

        # Step 2: Compute valid proposal digest
        from dual_pipeline.core import sha256_file
        src_hash = sha256_file(src_file)
        out_hashes = {"03_graph": sha256_file(f3)}
        intake_hash = sha256_file(pkg)

        proposal_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Doc Run",
            source_hash=src_hash,
            output_hashes=out_hashes,
            intake_hash=intake_hash,
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-test-001",
        )

        proposal = MutationProposal(
            proposal_id="prop-doc_run",
            meeting_name="Doc Run",
            source_hash=src_hash,
            output_hashes=out_hashes,
            intake_hash=intake_hash,
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root),
            expected_base_generation="gen-test-001",
            deltas={"nodes_added": 1},
            validation_evidence={"qa": "PASS"},
            rollback_scope="HEAD",
        )
        req_id = "req-doc-run-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": proposal_digest,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "human_operator",
            "meeting_name": "Doc Run",
            "expected_base_generation": "gen-test-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        # Step 3: Authorization validates successfully
        auth_info = validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertTrue(auth_info["authorized"])
        self.assertEqual(auth_info["status"], ProposalStatus.APPLYING.value)

        # Step 4: Mark activated into generation
        mark_mutation_activated(req_id, self.runtime_root, "gen-test-002", {"applied": 1})
        stored = load_approval_request(req_id, self.runtime_root)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], ProposalStatus.ACTIVATED.value)
        self.assertEqual(stored["activated_generation_id"], "gen-test-002")

        # Step 5: Idempotent replay
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-test-002", "graph_path": self.isolated_db_path}),
            encoding="utf-8",
        )
        meta["approval_receipt"] = stored
        replay_info = validate_mutation_authorization(
            self.out_dir, meta, written, runtime_root=self.runtime_root, allow_idempotent_recovery=True,
        )
        self.assertTrue(replay_info["authorized"])
        self.assertTrue(replay_info["idempotent"])
        self.assertEqual(replay_info["status"], ProposalStatus.ACTIVATED.value)

    def test_modified_deliverables_trigger_digest_mismatch(self) -> None:
        src_file = self.out_dir / "report.xlsx"
        src_file.write_bytes(b"PK\x03\x04original_bytes")
        f3 = self.out_dir / "03_graph.json"
        f3.write_text(json.dumps({"data": "v1"}), encoding="utf-8")
        pkg = self.out_dir / "intake_package.json"
        pkg.write_text(json.dumps({"pkg": "v1"}), encoding="utf-8")

        written = {"source_file": str(src_file), "03_graph": str(f3), "intake_package": str(pkg)}
        meta = {
            "meeting_name": "Doc Drift",
            "runtime_root": str(self.runtime_root),
            "graph_path": self.isolated_db_path,
            "enable_attachment_mutation_automation": True,
        }

        from dual_pipeline.core import sha256_file
        orig_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Doc Drift",
            source_hash=sha256_file(src_file),
            output_hashes={"03_graph": sha256_file(f3)},
            intake_hash=sha256_file(pkg),
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-test-001",
        )

        req_id = "req-drift-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": orig_digest,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "human_operator",
            "meeting_name": "Doc Drift",
            "expected_base_generation": "gen-test-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        # Tamper with deliverable bytes
        f3.write_text(json.dumps({"data": "tampered_v2"}), encoding="utf-8")

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(self.out_dir, meta, written, runtime_root=self.runtime_root)
        self.assertIn("AUTHORIZATION_DENIED_DIGEST_MISMATCH", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
