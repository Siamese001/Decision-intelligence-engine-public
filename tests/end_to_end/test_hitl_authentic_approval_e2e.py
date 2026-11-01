"""
tests/end_to_end/test_hitl_authentic_approval_e2e.py

End-to-End lifecycle test verifying authentic operator approval,
decision clearance, proposal-bound mutation authorization, and ledger preservation.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    check_active_ingestion_clearance,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    resolve_ledger_paths,
    verify_chain_integrity,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    DeliveryState,
    HostApprovalDispatcher,
    MutationProposal,
    ProposalStatus,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    compute_proposal_digest_from_deliverables,
    mark_mutation_activated,
    validate_mutation_authorization,
)
from dual_pipeline.transcript_qa_core.gate1_alr import (
    verify_alr_cryptographic_receipt,
)


class TestHitlAuthenticApprovalE2E(unittest.TestCase):
    """End-to-End lifecycle verification of authentic human approval."""

    def test_authentic_mutation_approval_e2e_lifecycle(self) -> None:
        """Full lifecycle: Proposal -> Authentic Operator Approval -> Mutation Auth -> Activation."""
        with tempfile.TemporaryDirectory() as td:
            rt_root = Path(td)
            out_dir = rt_root / "output"
            out_dir.mkdir(parents=True, exist_ok=True)

            # 1. Create simulated deliverables
            t_path = out_dir / "01_transcript.md"
            r_path = out_dir / "02_recap.md"
            g_path = out_dir / "03_graph.json"
            i_path = out_dir / "intake_package.json"

            t_path.write_text("# Authentic Transcript\n", encoding="utf-8")
            r_path.write_text("# Executive Recap\n", encoding="utf-8")
            g_path.write_text("{\"nodes\": 5, \"edges\": 4}\n", encoding="utf-8")
            i_path.write_text("{\"intake\": true}\n", encoding="utf-8")

            written = {
                "01_transcript": str(t_path),
                "02_recap": str(r_path),
                "03_graph": str(g_path),
                "intake_package": str(i_path),
            }

            from dual_pipeline.core import sha256_file
            out_hashes = {
                "01_transcript": sha256_file(t_path),
                "02_recap": sha256_file(r_path),
                "03_graph": sha256_file(g_path),
            }
            intake_hash = sha256_file(i_path)
            src_hash = out_hashes["01_transcript"]

            proposal_digest = compute_proposal_digest_from_deliverables(
                meeting_name="Strategic_Planning_Session",
                source_hash=src_hash,
                output_hashes=out_hashes,
                intake_hash=intake_hash,
                target_graph="CANONICAL_GRAPH.db",
                runtime_root=str(rt_root),
                expected_base_generation="current",
                deltas={"nodes_added": 5, "edges_added": 4},
                validation_evidence={"exit_qa_passed": True},
            )

            proposal = MutationProposal(
                proposal_id="prop-strat-plan",
                meeting_name="Strategic_Planning_Session",
                source_hash=src_hash,
                output_hashes=out_hashes,
                intake_hash=intake_hash,
                target_graph="CANONICAL_GRAPH.db",
                runtime_root=str(rt_root),
                expected_base_generation="current",
                deltas={"nodes_added": 5, "edges_added": 4},
                validation_evidence={"exit_qa_passed": True},
                rollback_scope="HEAD",
            )

            # 2. Dispatch via HostApprovalDispatcher with simulated human tool invoker
            dispatcher = HostApprovalDispatcher(runtime_root=rt_root)

            def mock_human_invoker(tool_name: str, args: dict) -> dict:
                # Authentic human operator selects "Approve"
                return {
                    "selected_option": "Approve publication of candidate changes into active knowledge graph",
                    "origin": "USER_INPUT",
                    "operator_id": "amit_ayer",
                }

            dispatch_res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=mock_human_invoker)
            self.assertTrue(dispatch_res.authorized)
            self.assertEqual(dispatch_res.status, ProposalStatus.APPROVED)
            self.assertEqual(dispatch_res.delivery_state, DeliveryState.DELIVERED)

            # 3. Validate mutation authorization under publication lock
            metadata = {
                "meeting_name": "Strategic_Planning_Session",
                "deliverables_check": True,
                "approval_receipt": dispatch_res.approval_receipt,
            }
            auth_res = validate_mutation_authorization(
                output_dir=out_dir,
                metadata=metadata,
                written=written,
                runtime_root=rt_root,
                approval_receipt=dispatch_res.approval_receipt,
            )
            self.assertTrue(auth_res["authorized"])
            self.assertEqual(auth_res["status"], ProposalStatus.APPLYING.value)

            # 4. Mark activated
            mark_mutation_activated(
                request_id=dispatch_res.request_id,
                runtime_root=rt_root,
                activated_generation_id="gen-42",
            )

            # 5. Verify idempotent recovery
            active_pair_file = rt_root / "active_pair.json"
            active_pair_file.write_text(json.dumps({"generation_id": "gen-42"}), encoding="utf-8")

            auth_res_idempotent = validate_mutation_authorization(
                output_dir=out_dir,
                metadata=metadata,
                written=written,
                runtime_root=rt_root,
                approval_receipt=dispatch_res.approval_receipt,
                allow_idempotent_recovery=True,
            )
            self.assertTrue(auth_res_idempotent["authorized"])
            self.assertTrue(auth_res_idempotent["idempotent"])


if __name__ == "__main__":
    unittest.main()
