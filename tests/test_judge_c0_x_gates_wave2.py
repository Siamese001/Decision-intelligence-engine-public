"""
tests/test_judge_c0_x_gates_wave2.py

Wave 2 Verification Suite for Meeting Output LLM-as-a-Judge Hardening.
Verifies:
1. Canonical CLI path executes C0.5 (Title Completeness) and halts before synthesis on unverified/placeholder titles.
2. Canonical CLI path executes C0.6 (GraphDB Role Verification) and binds verification receipts.
3. Canonical verify_exit_qa executes real XExitGatesEvaluator (X1-X3) on synthesized deliverables.
4. X2 permits exact 'Unassigned (Pending Owner Assignment)' per V5.8 C-25 while rejecting other unassigned variants.
5. X3 enforces strict cross-output coherence (attendee parity, quote pointer span integrity, canonical HAS_ROLE).
6. Fail-closed gate lock: Deterministic QA / X gate failures cannot be bypassed by LLM scores.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.canonical_pipeline.publish_helper import verify_exit_qa
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    MeetingXExitContract,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import C05TitleCompletenessGate
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator
from dual_pipeline.unified_agentic_pipeline import PipelineStage


class TestJudgeC0XGatesWave2(unittest.TestCase):
    """Verifies Wave 2 deterministic C0, QA, and X-exit gate alignments."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_p = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Canonical C0.5 Title Completeness Gate
    # -------------------------------------------------------------------------

    def test_canonical_c05_halts_before_synthesis_on_placeholder_role(self) -> None:
        """Canonical meeting route executes C0.5 and raises HITLRequiredError on placeholder title."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        meta = {
            "meeting_name": "Architecture Review",
            "date": "10/09/2026",
            "time": "14:00",
            "attendees": ["Alice (Participant)"],
            "diarization_status": "NATIVE",
        }
        source_text = "Alice: We need to provision the VPC today."

        with patch("dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity", return_value=None):
            with self.assertRaises(HITLRequiredError) as ctx:
                pipeline._execute_c0_pa(
                    CanonicalModality.MEETING_TRANSCRIPT,
                    source_text,
                    "hash-123",
                    meta,
                    None,
                    self.out_p,
                    tracker=MagicMock(),
                )
            self.assertIn("Title Resolution", str(ctx.exception))

    def test_canonical_c05_passes_on_verified_professional_title(self) -> None:
        """Canonical meeting route executes C0.5 and clears when title is non-placeholder."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        meta = {
            "meeting_name": "Architecture Review",
            "date": "10/09/2026",
            "time": "14:00",
            "attendees": ["Alice (Principal Cloud Architect)"],
            "diarization_status": "NATIVE",
            "hitl_role_resolutions": {"Alice": "Principal Cloud Architect"},
        }
        source_text = "Alice: We need to provision the VPC today."

        tracker = MagicMock()
        pa_ctx, src = pipeline._execute_c0_pa(
            CanonicalModality.MEETING_TRANSCRIPT,
            source_text,
            "hash-123",
            meta,
            None,
            self.out_p,
            tracker=tracker,
        )
        self.assertIsNotNone(pa_ctx)
        self.assertEqual(src, source_text)

    # -------------------------------------------------------------------------
    # 2. Canonical C0.6 GraphDB Role Verification Gate
    # -------------------------------------------------------------------------

    def test_canonical_c06_binds_verification_receipt_to_metadata(self) -> None:
        """Canonical route runs C0.6 and creates graph_role_verification_receipt."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        meta = {
            "meeting_name": "Strategy Sync",
            "date": "10/09/2026",
            "time": "10:00",
            "attendees": ["Bob (Solutions Director)"],
            "diarization_status": "NATIVE",
            "hitl_role_resolutions": {"Bob": "Solutions Director"},
        }
        source_text = "Bob: Strategy aligns with enterprise goals."

        pa_ctx, _ = pipeline._execute_c0_pa(
            CanonicalModality.MEETING_TRANSCRIPT,
            source_text,
            "hash-456",
            meta,
            None,
            self.out_p,
            tracker=MagicMock(),
        )
        self.assertIn("graph_role_verification_receipt", meta)
        receipt = meta["graph_role_verification_receipt"]
        self.assertEqual(receipt["gate"], "C0.6_GRAPHDB_ROLE_VERIFICATION")
        self.assertEqual(receipt["status"], "CLEARED")

    # -------------------------------------------------------------------------
    # 3. Canonical verify_exit_qa Executes Real XExitGatesEvaluator
    # -------------------------------------------------------------------------

    def test_verify_exit_qa_executes_real_x_gates_and_fails_on_schema_violation(self) -> None:
        """verify_exit_qa executes XExitGatesEvaluator and raises on invalid schema_version."""
        tracker = MagicMock()
        eval_result = MagicMock(authoritative_write_authorized=True)

        synth = SynthesisResult(
            output_1="Alice: We deploy the VPC today.",
            output_2="""# 1. Meeting Objective
Align on cloud network architecture.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC peering | Alice | 10/15/2026 |
""",
            output_3=json.dumps({"schema_version": "5.6"}),  # Invalid schema version
        )

        with self.assertRaises(ValueError) as ctx:
            verify_exit_qa(
                tracker=tracker,
                out_p=self.out_p,
                eval_result=eval_result,
                attempt=1,
                synthesis_result=synth,
                metadata={"meeting_name": "Test Meeting", "attendees": ["Alice"]},
            )
        self.assertIn("schema_version 5.7 constraint", str(ctx.exception))
        tracker.complete_stage.assert_called_with(PipelineStage.X_EXIT_GATES, status="FAIL", details={"error": str(ctx.exception)})

    def test_verify_exit_qa_fails_on_anti_sprawl_duplicate_entities(self) -> None:
        """verify_exit_qa triggers X1 anti-sprawl failure when duplicate entity IDs are present."""
        tracker = MagicMock()
        eval_result = MagicMock(authoritative_write_authorized=True)

        duplicate_entities_graph = json.dumps({
            "schema_version": "5.7",
            "meeting_id": "meet-1",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice", "mutation": "CREATE"},
                    {"id": "person:alice", "type": "person", "name": "Alice", "mutation": "CONFIRM"},
                ]
            },
            "evidence_only_statement_ids": [],
        })

        synth = SynthesisResult(
            output_1="Alice: Testing sprawl.",
            output_2="""# 1. Meeting Objective
Test objective.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC peering | Alice | 10/15/2026 |
""",
            output_3=duplicate_entities_graph,
        )

        with self.assertRaises(ValueError) as ctx:
            verify_exit_qa(
                tracker=tracker,
                out_p=self.out_p,
                eval_result=eval_result,
                attempt=1,
                synthesis_result=synth,
                metadata={"meeting_name": "Test Meeting", "attendees": ["Alice"]},
            )
        self.assertIn("Duplicate entity ID 'person:alice'", str(ctx.exception))

    def test_verify_exit_qa_fails_on_empty_output_3_even_if_auth_is_true(self) -> None:
        """verify_exit_qa fails closed when Output 03 is empty even if authoritative_write_authorized is True."""
        tracker = MagicMock()
        eval_result = MagicMock(authoritative_write_authorized=True)
        synth = SynthesisResult(
            output_1="Alice: Valid transcript.",
            output_2="# 1. Meeting Objective\nValid recap.\n# 2. Action & Governance Ledger\n| 1 | Work | Task | Alice | 10/10/2026 |",
            output_3="",
        )
        with self.assertRaises(ValueError) as ctx:
            verify_exit_qa(
                tracker=tracker,
                out_p=self.out_p,
                eval_result=eval_result,
                attempt=1,
                synthesis_result=synth,
                metadata={"meeting_name": "Test Meeting"},
            )
        self.assertIn("Output 03 canonical signal projection is missing or empty", str(ctx.exception))
        tracker.complete_stage.assert_called_with(
            PipelineStage.X_EXIT_GATES,
            status="FAIL",
            details={"error": "X1 FAIL: Output 03 canonical signal projection is missing or empty"},
        )

    def test_verify_exit_qa_fails_on_corrupt_disk_output_3_when_synthesis_result_omitted(self) -> None:
        """verify_exit_qa scans out_p when synthesis_result is None and fails on corrupt Output 03."""
        tracker = MagicMock()
        eval_result = MagicMock(authoritative_write_authorized=True)
        (self.out_p / "03_corrupt_canonical_signal_projection.json").write_text("INVALID_JSON", encoding="utf-8")
        (self.out_p / "02_test_executive_recap.md").write_text("# 1. Meeting Objective\nTest.\n# 2. Action & Governance Ledger\n| 1 | W | T | Alice | 10/10/2026 |", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            verify_exit_qa(
                tracker=tracker,
                out_p=self.out_p,
                eval_result=eval_result,
                attempt=1,
                synthesis_result=None,
                metadata={"meeting_name": "Test Meeting"},
            )
        self.assertIn("X1 FAIL: Invalid JSON in graph output", str(ctx.exception))
        tracker.complete_stage.assert_called_with(
            PipelineStage.X_EXIT_GATES,
            status="FAIL",
            details={"error": str(ctx.exception)},
        )

    # -------------------------------------------------------------------------
    # 4. X2 Action Items: Permitted Pending Owner vs Unassigned Rejection
    # -------------------------------------------------------------------------

    def test_x2_permits_exact_pending_owner_format(self) -> None:
        """X2 cleanly passes 'Unassigned (Pending Owner Assignment)' per V5.8 Rule C-25."""
        recap = """# 1. Meeting Objective
Network architecture review.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC routing tables | Unassigned (Pending Owner Assignment) | 10/15/2026 |
"""
        evaluator = XExitGatesEvaluator()
        evaluator._validate_recap_structure(recap)  # Should not raise

    def test_x2_rejects_unapproved_unassigned_variants(self) -> None:
        """X2 rejects variants like 'Unassigned' or 'Pending' that lack the full approved token."""
        for bad_owner in ("Unassigned", "Pending", "TBC", "Unknown", "Not assigned", "Speaker A"):
            recap = f"""# 1. Meeting Objective
Network architecture review.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC routing tables | {bad_owner} | 10/15/2026 |
"""
            evaluator = XExitGatesEvaluator()
            with self.assertRaises(ValueError) as ctx:
                evaluator._validate_recap_structure(recap)
            self.assertIn("Anti-embarrassment gate triggered", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 5. X3 Cross-Output Coherence Invariants
    # -------------------------------------------------------------------------

    def test_x3_rejects_attendee_missing_from_output3_entity_upserts(self) -> None:
        """X3 rejects when roster attendee is not upserted in Output 03 graph entities."""
        f1_text = "Alice: Hi everyone. Bob: Hello."
        f2_text = """# 1. Meeting Objective
Review.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Review VPC peering | Alice | 10/15/2026 |
"""
        f3_data = {
            "schema_version": "5.7",
            "meeting_id": "meet-1",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ]
            },
            "evidence_only_statement_ids": [],
        }

        evaluator = XExitGatesEvaluator()
        # Bob is in attendees but missing from entity_upserts
        with self.assertRaises(ValueError) as ctx:
            evaluator.execute(
                recap=f2_text,
                graph_json=json.dumps(f3_data),
                metadata={"meeting_name": "Review", "attendees": ["Alice", "Bob"]},
                transcript=f1_text,
            )
        self.assertIn("X3 FAIL", str(ctx.exception))
        self.assertIn("Bob", str(ctx.exception))

    def test_x3_rejects_quote_pointer_span_length_mismatch(self) -> None:
        """X3 rejects when evidence quote pointer span start/end length does not match exact_text length."""
        f1_text = "Alice: We must provision the VPC peering mesh today."
        f2_text = """# 1. Meeting Objective
Review.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC peering | Alice | 10/15/2026 |
"""
        f3_data = {
            "schema_version": "5.7",
            "meeting_id": "meet-1",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {
                "ev-01": {
                    "text": "Alice",
                    "quote_pointer": [0, 20],  # span 20 != text len 5
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ]
            },
            "evidence_only_statement_ids": [],
        }

        evaluator = XExitGatesEvaluator()
        with self.assertRaises(ValueError) as ctx:
            evaluator.execute(
                recap=f2_text,
                graph_json=json.dumps(f3_data),
                metadata={"meeting_name": "Review", "attendees": ["Alice"]},
                transcript=f1_text,
            )
        self.assertIn("span length", str(ctx.exception))

    def test_x3_rejects_deprecated_has_role_in_predicate(self) -> None:
        """X3 rejects deprecated HAS_ROLE_IN and mandates canonical HAS_ROLE."""
        f1_text = "Alice: I lead cloud architecture."
        f2_text = """# 1. Meeting Objective
Review.
# 2. Action & Governance Ledger
| # | Workstream | Action Item | Owner | Target Date |
| 1 | Infra | Deploy VPC peering | Alice | 10/15/2026 |
"""
        f3_data = {
            "schema_version": "5.7",
            "meeting_id": "meet-1",
            "classification": "internal",
            "audiences": [],
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:alice", "type": "person", "name": "Alice"}
                ],
                "relationship_signals": [
                    {"from_id": "person:alice", "relationship": "HAS_ROLE_IN", "to_id": "role:architect"}
                ],
            },
            "evidence_only_statement_ids": [],
        }

        evaluator = XExitGatesEvaluator()
        with self.assertRaises(ValueError) as ctx:
            evaluator.execute(
                recap=f2_text,
                graph_json=json.dumps(f3_data),
                metadata={"meeting_name": "Review", "attendees": ["Alice"]},
                transcript=f1_text,
            )
        self.assertIn("HAS_ROLE_IN", str(ctx.exception))
        self.assertIn("canonical 'HAS_ROLE'", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 6. Fail-Closed Authority Gate Lock
    # -------------------------------------------------------------------------

    def test_llm_judge_score_cannot_clear_deterministic_qa_failure(self) -> None:
        """Proof that a 100/100 judge score cannot authorize release when deterministic QA/X fails."""
        tracker = MagicMock()
        # Simulated false-pass judge that gives 100 but authoritative_write_authorized is False due to QA failure
        eval_result = MagicMock(
            authoritative_write_authorized=False,
            qa_report={"output_01_forensic_transcript": {"failures": ["Speaker collapse detected: 92% >= 85%"]}},
            final_decision={"status": "FAIL"},
        )

        with self.assertRaises(RuntimeError) as ctx:
            verify_exit_qa(
                tracker=tracker,
                out_p=self.out_p,
                eval_result=eval_result,
                attempt=1,
            )
        self.assertIn("Speaker collapse detected", str(ctx.exception))
        tracker.complete_stage.assert_called_with(PipelineStage.X_EXIT_GATES, status="FAIL")


if __name__ == "__main__":
    unittest.main()
