"""
tests/adversarial/test_hitl_autonomous_bypass_reproduction_adversarial.py

Adversarial reproduction test suite for HITL thresholds, ask_user_question dispatch,
approval identity, decision scope, and resume behavior.
Demonstrates reproduction and enforcement against autonomous bypass vectors.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    HostApprovalDispatcher,
    MutationProposal,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    validate_approval_response_signature,
)
from dual_pipeline.hitl_governance_core.crypto import (
    append_signed_approval_to_ledger,
    compute_alr_subject_digest,
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)
from dual_pipeline.hitl_governance_core.decision_clearance_core.models import (
    ScopedDecision,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    resolve_ledger_paths,
    verify_chain_integrity,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    validate_mutation_authorization,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_min_autonomous_confidence_floor,
    needs_operator_decision,
)
from dual_pipeline.speaker_attribution_core.collapse_detector import (
    _is_valid_collapse_entry,
)
from dual_pipeline.transcript_qa_core.gate1_alr import (
    verify_alr_cryptographic_receipt,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    verify_mandatory_attendee_confirmation,
)


class TestHitlAutonomousBypassReproductionAdversarial(unittest.TestCase):
    """Adversarial suite reproducing and locking down HITL autonomous bypass vectors."""

    def test_reproduce_bypass_synthetic_origin_with_user_command_intent(self) -> None:
        """Vector 1: Synthetic approval carrying execution intent must be rejected."""
        malicious_payload = {
            "source": "synthetic_approval",
            "origin": "mock_operator",
            "user_command": "proceed with implementation",
        }
        is_valid, msg = validate_approval_origin(malicious_payload)
        self.assertFalse(
            is_valid,
            f"Autonomous bypass: Synthetic marker was accepted due to user_command! ({msg})",
        )
        self.assertIn("APPROVAL_REJECTED_SYNTHETIC_ORIGIN", msg)

    def test_reproduce_bypass_scoped_decision_requires_asking_low_confidence(self) -> None:
        """Vector 2: Decision with top confidence below conviction floor must require asking."""
        # Top confidence 0.40, second 0.05 -> gap 0.35 (> 0.30 threshold), but top < 0.65 floor
        dec = ScopedDecision(
            decision_id="dec-low-conf",
            title="Low confidence decision",
            wave=1,
            top_confidence=0.40,
            second_confidence=0.05,
            handling="Resolved autonomously",
            status="OPEN",
            question="Which low-confidence branch should be selected?",
            options=[
                {"label": "Branch A", "confidence_score": 0.40},
                {"label": "Branch B", "confidence_score": 0.05},
            ],
        )
        self.assertTrue(
            dec.requires_asking,
            "Autonomous bypass: ScopedDecision with top confidence below 0.65 floor did not require asking!",
        )

    def test_reproduce_bypass_gate1_alr_autonomous_approved_decision(self) -> None:
        """Vector 3: Gate 1.7 must reject (Operator Verified) backed by AUTONOMOUS_APPROVED or engine."""
        with tempfile.TemporaryDirectory() as td:
            lp = Path(td) / "hitl_decision_ledger.json"
            lp.write_text(json.dumps({"decisions": []}), encoding="utf-8")
            priv, pub = generate_hitl_keypair()
            subject_dict = {
                "meeting_name": "test_meeting",
                "meeting_date": "09/28/2026",
                "project": "nea",
                "attendees": ["Alice", "Bob"],
            }
            subj_digest = compute_alr_subject_digest(subject_dict)

            # Malicious entry claiming autonomous approval
            entry = create_signed_hitl_approval_entry(
                subject_sha256=subj_digest,
                case_ids=["test_case"],
                decision="AUTONOMOUS_APPROVED",
                operator_id="engine_autonomous_authority_01",
                meeting_slug="test_meeting",
                private_key=priv,
                public_key=pub,
            )
            append_signed_approval_to_ledger(entry, ledger_path=lp)

            f1_text = (
                "# Executive Session -- HITL-Resolved Transcript\n"
                "**Date:** 09/28/2026\n"
                "**Participants:** Alice (PartnerAdvisory), Bob (PartnerAdvisory)\n---\n"
                "### [00:01:00]\n**Alice:** Hi\n"
                "## Ambiguity Lock Register\n"
                "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
                "|---|---|---|---|---|---|\n"
                "| 1 | Speaker | [00:00:00] | stmt-1 | Alice | (Operator Verified) |\n"
            )
            meta = {
                "meeting_name": "test_meeting",
                "date": "09/28/2026",
                "project": "nea",
                "roster": [{"name": "Alice"}, {"name": "Bob"}],
                "hitl_ledger_path": str(lp),
            }
            passed, details, failures = verify_alr_cryptographic_receipt(
                f1_text, meta, ledger_path=lp
            )
            self.assertFalse(
                passed,
                "Autonomous bypass: Gate 1.7 accepted AUTONOMOUS_APPROVED engine entry for (Operator Verified)!",
            )
            self.assertFalse(details.get("hitl_receipt_bound", False))

    def test_reproduce_bypass_attendee_gate_unauthenticated_metadata(self) -> None:
        """Vector 4: Attendee confirmation requires cryptographic subject binding."""
        metadata = {
            "input_path": "audio.mp3",
            "attendees_confirmed_by_operator": True,
            "attendee_confirmation_receipt": {"status": "RESOLVED_HITL"},
            # Missing subject_sha256 cryptographic binding
        }
        with self.assertRaises(ValueError) as ctx:
            verify_mandatory_attendee_confirmation(metadata, "")
        self.assertIn("unauthenticated", str(ctx.exception).lower())

    def test_reproduce_bypass_mutation_authorization_missing_proposal_digest(self) -> None:
        """Vector 5: Mutation authorization must reject deliverable validation when proposal digest is missing."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "output"
            out_dir.mkdir()
            approval_receipt = {
                "request_id": "req-test",
                "status": "APPROVED",
                "authorized": True,
                "operator_origin": "operator",
                "meeting_name": "test_meeting",
                "expected_base_generation": "current",
            }
            written = {
                "01_transcript": str(out_dir / "01.md"),
                "02_recap": str(out_dir / "02.md"),
                "03_graph": str(out_dir / "03.json"),
                "intake_package": str(out_dir / "intake_package.json"),
            }
            (out_dir / "01.md").write_text("Tampered transcript", encoding="utf-8")
            (out_dir / "02.md").write_text("Tampered recap", encoding="utf-8")
            (out_dir / "03.json").write_text("{}", encoding="utf-8")
            (out_dir / "intake_package.json").write_text("{}", encoding="utf-8")
            metadata = {"meeting_name": "test_meeting", "deliverables_check": True}

            with self.assertRaises(MutationAuthorizationError) as ctx:
                validate_mutation_authorization(
                    output_dir=out_dir,
                    metadata=metadata,
                    written=written,
                    runtime_root=out_dir,
                    approval_receipt=approval_receipt,
                )
            self.assertIn("DIGEST_MISMATCH", str(ctx.exception))

    def test_reproduce_bypass_resume_ingestion_unauthenticated_operator(self) -> None:
        """Vector 6: Ingestion resume rejects responses without authentic operator identity."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lp = root / "hitl_decision_ledger.json"
            lp.write_text(json.dumps({"decisions": []}), encoding="utf-8")
            req = build_active_ingestion_approval_request(
                run_id="run-123",
                directive_id="dir-123",
                runtime_root=str(root),
                project="test_proj",
                source_package_digest="src-123",
                proposed_delta_digest="delta-123",
                baseline_generation=1,
            )
            req_id = req["request_id"]
            req_dir = root / "approval_requests"
            req_dir.mkdir(parents=True, exist_ok=True)
            (req_dir / f"{req_id}.json").write_text(json.dumps(req), encoding="utf-8")

            # Malicious unauthenticated response
            unauth_response = {
                "selected_option": req["question_payload"]["options"][0]["label"],
                # Missing operator_id and authentic signature
            }
            ok, msg, _ = resume_ingestion_with_approval(
                request_id=req_id,
                operator_response=unauth_response,
                storage_root=root,
                ledger_path=lp,
            )
            self.assertFalse(
                ok,
                f"Autonomous bypass: Resume ingestion accepted unauthenticated response! ({msg})",
            )
            self.assertIn("UNAUTHENTICATED", msg.upper())

    def test_preserve_ledger_history_immutability(self) -> None:
        """Vector 7: Ledger history must remain fully preserved and chain integrity valid."""
        s_path, j_path = resolve_ledger_paths()
        if s_path.is_file():
            is_valid, msg = verify_chain_integrity(s_path)
            self.assertIn("records sequentially linked and valid", msg)

    def test_reproduce_bypass_negated_substring_selection_rejected(self) -> None:
        """Vector 8: Negated or substring evasions like 'do not approve' are rejected fail-closed."""
        allowed = ["Approve mutation", "Reject", "Review details"]
        # Test runtime tool adapter signature validation
        forged_response = {
            "selected_option": "do not approve",
            "operator_id": "human_operator_alice",
        }
        valid, msg = validate_approval_response_signature(forged_response, allowed)
        self.assertFalse(valid, "Substring evasion: 'do not approve' was accepted!")
        self.assertIn("OPTION_MISMATCH", msg)

        # Test host approval dispatcher process_response
        proposal = MutationProposal(
            proposal_id="prop-bypass-negate",
            meeting_name="test_meeting",
            source_hash="sha256:src",
            output_hashes={},
            intake_hash="sha256:intake",
            target_graph="CANONICAL_GRAPH.db",
            runtime_root="/tmp",
            expected_base_generation="1",
            deltas={},
            validation_evidence={},
            rollback_scope="HEAD",
        )
        with tempfile.TemporaryDirectory() as td:
            dispatcher = HostApprovalDispatcher(runtime_root=td)
            res = dispatcher.process_response(
                proposal=proposal,
                request_id="req-bypass-negate",
                tool_result={"selected_option": "do not approve", "origin": "USER_INPUT"},
            )
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "MALFORMED_SELECTION")

    def test_idempotent_single_persistence_on_repeated_resume(self) -> None:
        """Vector 9: Repeated resume calls persist exactly one decision record to the ledger."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lp = root / "hitl_decision_ledger.json"
            lp.write_text(json.dumps({"decisions": []}), encoding="utf-8")
            req = build_active_ingestion_approval_request(
                run_id="run-idemp-01",
                directive_id="dir-idemp-01",
                runtime_root=str(root),
                project="test_proj",
                source_package_digest="src-idemp-01",
                proposed_delta_digest="delta-idemp-01",
                baseline_generation=1,
            )
            req_id = req["request_id"]
            req_dir = root / "approval_requests"
            req_dir.mkdir(parents=True, exist_ok=True)
            (req_dir / f"{req_id}.json").write_text(json.dumps(req), encoding="utf-8")

            valid_resp = {
                "selected_option": req["question_payload"]["options"][0]["label"],
                "operator_id": "operator_alice",
            }
            # First resume attempt
            ok1, msg1, _ = resume_ingestion_with_approval(
                request_id=req_id,
                operator_response=valid_resp,
                storage_root=root,
                ledger_path=lp,
            )
            self.assertTrue(ok1)

            # Second resume attempt (same request)
            ok2, msg2, _ = resume_ingestion_with_approval(
                request_id=req_id,
                operator_response=valid_resp,
                storage_root=root,
                ledger_path=lp,
            )
            self.assertTrue(ok2)

            store = HITLDecisionStore(ledger_path=lp)
            history = store.get_history()
            matching_records = [r for r in history if r.get("decision_id") == req_id]
            self.assertEqual(
                len(matching_records),
                1,
                f"Duplicate persistence: Found {len(matching_records)} records for request {req_id}!",
            )

    def test_pre_approved_scope_skips_duplicate_prompt(self) -> None:
        """Vector 10: Dispatching request for pre-approved scope reuses clearance without prompting."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lp = root / "hitl_decision_ledger.json"
            lp.write_text(json.dumps({"decisions": []}), encoding="utf-8")

            # Seed ledger with prior approval
            store = HITLDecisionStore(ledger_path=lp)
            store.record_decision(
                decision_id="req-pre-01",
                category="INGESTION_MUTATION",
                prompt="Approve candidate publication?",
                options=[{"label": "Approve", "confidence_score": 0.95}],
                selected_option="Approve",
                operator_id="operator_bob",
                metadata={
                    "run_id": "run-pre-01",
                    "directive_id": "dir-pre-01",
                    "delta_digest": "delta-pre-01",
                    "outcome_type": "AFFIRMED",
                    "approval_origin": "operator_bob",
                },
            )

            req = build_active_ingestion_approval_request(
                run_id="run-pre-01",
                directive_id="dir-pre-01",
                runtime_root=str(root),
                project="test_proj",
                source_package_digest="src-pre-01",
                proposed_delta_digest="delta-pre-01",
                baseline_generation=1,
            )

            transport_called = False

            def mock_transport(_):
                nonlocal transport_called
                transport_called = True
                return {"result": {"selected_option": "Approve"}}

            dispatched = dispatch_and_persist_approval_request(
                req,
                transport=mock_transport,
                ledger_path=lp,
                storage_root=root,
            )
            self.assertFalse(
                transport_called,
                "Duplicate prompt: Transport was called for already cleared scope!",
            )
            self.assertEqual(dispatched["dispatch_status"], "PRE_APPROVED")
            self.assertEqual(dispatched["response_status"], "APPROVED")


if __name__ == "__main__":
    unittest.main()
