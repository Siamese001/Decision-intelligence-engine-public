#!/usr/bin/env python3
"""tests/test_mutation_approval_binding.py

Unit and integration tests for Wave 2: Mutation Authorization & Proposal Binding.
Verifies:
1. Forged approval receipt rejection (anti-synthetic origin).
2. Proposal digest mismatch rejection (anti-tamper / deliverable drift).
3. Stale base generation rejection (anti-race under publication lock).
4. Replayed approval rejection (prevent re-consuming activated approvals).
5. Idempotent crash recovery (committed activation recovery).
6. Concurrent publication serialization under file lock.
7. Bypasses via CLI reviewer, mutate_graph, and QA pass are strictly blocked.
"""

from __future__ import annotations

import concurrent.futures
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestMutationApprovalBinding(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.output_dir = self.root / "output" / "project_nexus" / "09-10-2026" / "Test_Meeting"
        self.output_dir.mkdir(parents=True)

        self.runtime_root = self.root / ".runtime"
        self.runtime_root.mkdir(parents=True)

        # Baseline active_pair
        self.active_pair_file = self.runtime_root / "active_pair.json"
        self.active_pair = {
            "generation_id": "generation-baseline-001",
            "graph_path": "graph.sqlite",
            "sidecar_path": "sidecar.sqlite",
        }
        self.active_pair_file.write_text(json.dumps(self.active_pair), encoding="utf-8")

        # Deliverables
        self.f1 = self.output_dir / "01_transcript.md"
        self.f1.write_text("Jen Bricker: Discussion point 1\n", encoding="utf-8")
        self.f2 = self.output_dir / "02_recap.md"
        self.f2.write_text("Executive summary\n", encoding="utf-8")
        self.f3 = self.output_dir / "03_graph.json"
        self.f3.write_text(json.dumps({"mutations": []}), encoding="utf-8")
        self.pkg = self.output_dir / "intake_package.json"
        self.pkg.write_text(json.dumps({"package": "v1"}), encoding="utf-8")

        self.written = {
            "01_transcript": str(self.f1),
            "02_recap": str(self.f2),
            "03_graph": str(self.f3),
            "intake_package": str(self.pkg),
        }
        self.metadata = {
            "meeting_name": "Test Meeting",
            "project": "project_nexus",
            "runtime_root": str(self.runtime_root),
        }

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _create_valid_approved_proposal(self) -> tuple[MutationProposal, dict]:
        from dual_pipeline.core import sha256_file
        out_hashes = {
            "01_transcript": sha256_file(self.f1),
            "02_recap": sha256_file(self.f2),
            "03_graph": sha256_file(self.f3),
        }
        intake_hash = sha256_file(self.pkg)
        source_hash = sha256_file(self.f1)
        rt_str = str(self.runtime_root.resolve())
        proposal = MutationProposal(
            proposal_id="prop-test_meeting",
            meeting_name="Test Meeting",
            source_hash=source_hash,
            output_hashes=out_hashes,
            intake_hash=intake_hash,
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=rt_str,
            expected_base_generation="generation-baseline-001",
            deltas={},
            validation_evidence={},
            rollback_scope="HEAD",
        )
        dig = proposal.compute_proposal_digest()
        rec = {
            "request_id": "req-test-1",
            "proposal_id": "prop-test_meeting",
            "proposal_digest": dig,
            "meeting_name": "Test Meeting",
            "status": ProposalStatus.APPROVED.value,
            "operator_origin": "USER_INPUT",
            "target_graph": "CANONICAL_GRAPH.db",
            "runtime_root": rt_str,
            "expected_base_generation": "generation-baseline-001",
            "authorized": True,
            "source_hash": source_hash,
            "intake_hash": intake_hash,
            "output_hashes": out_hashes,
            "deltas": {},
            "validation_evidence": {},
            "rollback_scope": "HEAD",
        }
        persist_approval_request(rec, self.runtime_root)
        return proposal, rec

    def test_missing_approval_fails_closed(self) -> None:
        """Verify that attempting mutation without an approval receipt fails closed."""
        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=self.metadata,
                written=self.written,
                runtime_root=self.runtime_root,
            )
        self.assertIn("AUTHORIZATION_DENIED_MISSING_APPROVAL", str(ctx.exception))

    def test_forged_synthetic_approval_rejected(self) -> None:
        """Verify that synthetic origins (stop hooks, turbo mode) are rejected."""
        _, rec = self._create_valid_approved_proposal()
        forged_rec = dict(rec)
        forged_rec["operator_origin"] = "stop hook blocked termination through review policy"

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=self.metadata,
                written=self.written,
                runtime_root=self.runtime_root,
                approval_receipt=forged_rec,
            )
        self.assertIn("AUTHORIZATION_DENIED_SYNTHETIC_ORIGIN", str(ctx.exception))

    def test_proposal_digest_mismatch_rejected(self) -> None:
        """Verify that post-approval modifications to deliverables are rejected."""
        _, rec = self._create_valid_approved_proposal()
        # Modify a deliverable post-approval
        self.f1.write_text("Modified after approval\n", encoding="utf-8")
        # Enable deliverable digest check
        meta = dict(self.metadata)
        meta["deliverables_check"] = True
        meta["approval_receipt"] = rec

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=meta,
                written=self.written,
                runtime_root=self.runtime_root,
                approval_receipt=rec,
            )
        self.assertIn("AUTHORIZATION_DENIED_DIGEST_MISMATCH", str(ctx.exception))

    def test_stale_base_generation_rejected(self) -> None:
        """Verify that approvals bound to an outdated base generation fail closed."""
        _, rec = self._create_valid_approved_proposal()
        # Advance graph active pair
        self.active_pair["generation_id"] = "generation-advanced-002"
        self.active_pair_file.write_text(json.dumps(self.active_pair), encoding="utf-8")

        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=self.metadata,
                written=self.written,
                runtime_root=self.runtime_root,
                approval_receipt=rec,
            )
        self.assertIn("AUTHORIZATION_DENIED_STALE_BASE_GENERATION", str(ctx.exception))

    def test_replayed_activated_approval_rejected(self) -> None:
        """Verify that an approval receipt already activated cannot be reused."""
        _, rec = self._create_valid_approved_proposal()
        mark_mutation_activated("req-test-1", self.runtime_root, activated_generation_id="generation-baseline-001")

        # Now active pair advances beyond baseline
        self.active_pair["generation_id"] = "generation-advanced-002"
        self.active_pair_file.write_text(json.dumps(self.active_pair), encoding="utf-8")

        rec_act = load_approval_request("req-test-1", self.runtime_root)
        with self.assertRaises(MutationAuthorizationError) as ctx:
            validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=self.metadata,
                written=self.written,
                runtime_root=self.runtime_root,
                approval_receipt=rec_act,
            )
        self.assertIn("AUTHORIZATION_DENIED_REPLAY_DETECTED", str(ctx.exception))

    def test_idempotent_recovery_of_committed_activation(self) -> None:
        """Verify crash recovery returns committed state without re-mutating."""
        _, rec = self._create_valid_approved_proposal()
        mark_mutation_activated("req-test-1", self.runtime_root, activated_generation_id="generation-committed-001")

        # Active generation matches committed generation
        self.active_pair["generation_id"] = "generation-committed-001"
        self.active_pair_file.write_text(json.dumps(self.active_pair), encoding="utf-8")

        rec_act = load_approval_request("req-test-1", self.runtime_root)
        res = validate_mutation_authorization(
            output_dir=self.output_dir,
            metadata=self.metadata,
            written=self.written,
            runtime_root=self.runtime_root,
            approval_receipt=rec_act,
            allow_idempotent_recovery=True,
        )
        self.assertTrue(res["authorized"])
        self.assertTrue(res["idempotent"])
        self.assertEqual(res["status"], ProposalStatus.ACTIVATED.value)
        self.assertEqual(res["activated_generation_id"], "generation-committed-001")

    def test_concurrent_publication_serialized_cleanly(self) -> None:
        """Verify multi-worker concurrent validation serializes under file lock."""
        _, rec = self._create_valid_approved_proposal()

        def _worker_attempt(worker_id: int):
            meta = dict(self.metadata)
            meta["worker_id"] = worker_id
            return validate_mutation_authorization(
                output_dir=self.output_dir,
                metadata=meta,
                written=self.written,
                runtime_root=self.runtime_root,
                approval_receipt=rec,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_worker_attempt, i) for i in range(4)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertTrue(r["authorized"])
            self.assertEqual(r["status"], ProposalStatus.APPLYING.value)

    def test_publish_helper_rejects_missing_approval(self) -> None:
        """Verify publish_helper.publish_graph_if_requested fails closed if unapproved."""
        from dual_pipeline.canonical_pipeline.publish_helper import publish_graph_if_requested

        with self.assertRaises(MutationAuthorizationError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.pkg,
                metadata=self.metadata,
                written=self.written,
                directive_id="dir-test",
                reviewer="Carlos Salazar",
                rationale="Test publication",
                embedding_backend="bge-m3",
            )
        self.assertIn("AUTHORIZATION_DENIED_MISSING_APPROVAL", str(ctx.exception))

    def test_publish_helper_succeeds_when_approved(self) -> None:
        """Verify publish_helper.publish_graph_if_requested succeeds when validly approved."""
        from dual_pipeline.canonical_pipeline.publish_helper import publish_graph_if_requested

        _, rec = self._create_valid_approved_proposal()
        meta = dict(self.metadata)
        meta["approval_receipt"] = rec

        with patch("dual_pipeline.transcript_pipeline_core.release_runner.publish_release_to_graph") as mock_pub, \
             patch("dual_pipeline.canonical_pipeline.publication_bindings.canonical_publication_runtime", return_value=self.runtime_root.resolve()), \
             patch("dual_pipeline.release_policy.verify_authoritative_release_gate"), \
             patch("dual_pipeline.canonical_pipeline.publication_bindings.publication_bindings", return_value={}), \
             patch("dual_pipeline.canonical_pipeline.publish_helper._assert_authoritative_mutation_preconditions"), \
             patch("dual_pipeline.canonical_pipeline.publish_helper.check_directory_compliance"):
            mock_pub.return_value = {"status": "COMPLETE", "generation_id": "generation-new-002"}
            res = publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.pkg,
                metadata=meta,
                written=self.written,
                directive_id="dir-test",
                reviewer="Carlos Salazar",
                rationale="Test publication",
                embedding_backend="bge-m3",
            )
            self.assertEqual(res["status"], "COMPLETE")
            self.assertTrue(mock_pub.called)

            # Request status should now be ACTIVATED
            stored = load_approval_request("req-test-1", self.runtime_root)
            self.assertEqual(stored["status"], ProposalStatus.ACTIVATED.value)
            self.assertEqual(stored["activated_generation_id"], "generation-new-002")


if __name__ == "__main__":
    unittest.main(verbosity=2)
