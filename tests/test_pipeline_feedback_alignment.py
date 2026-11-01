"""Wave 4 Acceptance Test Suite: Pipeline Feedback Alignment.

Verifies end-to-end L1 post-execution and post-debate review contracts across
RawTranscriptPipeline (Pipeline A/B) and DecisionOrchestrator (Pipeline D).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dual_pipeline.decision.agents.decision_orchestrator import DecisionOrchestrator
from dual_pipeline.decision.agents.post_debate_reviewer import DecisionPostDebateReviewer
from dual_pipeline.l1_review_contracts import (
    L1ReviewVerdict,
)
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_pipeline_core.post_l2_review import (
    TranscriptL1PostExecutionReviewer,
)


class TestTranscriptPipelineFeedbackAlignment:
    """Tests L1 post-L2 review integration in RawTranscriptPipeline."""

    def test_transcript_l1_post_l2_review_pass(self) -> None:
        reviewer = TranscriptL1PostExecutionReviewer()
        artifacts = {
            "01_hitl_resolved_transcript.md": (
                "# Executive Architecture Alignment\n\n"
                "[00:01:00] Alice: We need to decouple the L1 and L2 pipelines.\n"
                "[00:02:00] Bob: Agreed. Bounded iterations prevent infinite loops.\n"
            ),
            "02_executive_recap.md": (
                "# Executive Recap\n\n"
                "**Meeting Objective**: Decouple L1 and L2 pipelines with bounded feedback.\n\n"
                "## Next Steps\n"
                "- Implement L1PostL2ReviewContract by end of week.\n"
            ),
            "03_canonical_signal_projection.json": json.dumps({
                "statements": [
                    {"id": "stmt-1", "text": "Decouple L1 and L2 pipelines", "kind": "decision"}
                ],
                "entities": [
                    {"id": "sys:l1_pipeline", "type": "system"}
                ],
            }),
        }
        metadata = {
            "meeting_name": "Architecture Alignment",
            "run_id": "run-test-01",
        }
        contract = reviewer.review_execution(
            artifacts=artifacts,
            plan=None,
            metadata=metadata,
            attempt=1,
        )

        assert contract.verdict == L1ReviewVerdict.SUFFICIENT
        assert contract.deterministic_passed is True
        assert contract.semantic_passed is True
        assert contract.verify_integrity() is True
        assert contract.canonical_digest["algorithm"] == "sha256"

    def test_transcript_l1_post_l2_review_detects_empty_outputs_and_generates_replan(self) -> None:
        reviewer = TranscriptL1PostExecutionReviewer()
        # Missing output 01 and invalid output 03
        artifacts = {
            "01_hitl_resolved_transcript.md": "",
            "02_executive_recap.md": "# Recap\nObjective met.",
            "03_canonical_signal_projection.json": "not valid json {{{",
        }
        metadata = {
            "meeting_name": "Defective Meeting",
            "run_id": "run-test-02",
        }
        contract = reviewer.review_execution(
            artifacts=artifacts,
            plan=None,
            metadata=metadata,
            attempt=1,
        )

        assert contract.verdict == L1ReviewVerdict.INSUFFICIENT_REPLAN
        assert contract.deterministic_passed is False
        assert contract.semantic_passed is False
        assert contract.replan_directive is not None
        assert contract.verify_integrity() is True

    def test_transcript_pipeline_executes_l1_post_l2_review_in_flight(self) -> None:
        pipeline = RawTranscriptPipeline(provider_type="offline")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_file = tmp_path / "meeting.txt"
            input_file.write_text(
                "[00:00:01] Sarah: Let's discuss the BGE-M3 model integration.\n"
                "[00:00:15] Mike: The 11 non-compensating gates must pass before activation.\n"
                "[00:00:30] Sarah: Confirmed, we will maintain steady-state caching.\n",
                encoding="utf-8",
            )
            output_dir = tmp_path / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            metadata = {
                "meeting_name": "BGE-M3 Review",
                "date": "09/12/2026",
                "time": "10:00 AM",
                "classification": "internal",
                "attendees": ["Sarah", "Mike"],
                "run_id": "run_tx_e2e",
            }

            pipeline.run(
                input_path=str(input_file),
                metadata=metadata,
                output_dir=str(output_dir),
            )

            assert "l1_post_l2_review" in metadata
            l1_rev = metadata["l1_post_l2_review"]
            assert l1_rev["verdict"] in ("SUFFICIENT", "INSUFFICIENT_REPLAN")
            assert "canonical_digest" in l1_rev
            assert l1_rev["deterministic_passed"] is True

            # Verify review artifact is also present in outputs
            review_file = output_dir / "l1_post_l2_review.json"
            if review_file.exists():
                with open(review_file, "r", encoding="utf-8") as rf:
                    saved_contract = json.load(rf)
                assert saved_contract["review_id"] == l1_rev["review_id"]


class TestDecisionOrchestratorFeedbackAlignment:
    """Tests L1 post-debate review integration in Pipeline D DecisionOrchestrator."""

    def test_decision_post_debate_reviewer_pass(self) -> None:
        reviewer = DecisionPostDebateReviewer()
        options = [
            {"key": "opt_a", "label": "Option A (Centralized)"},
            {"key": "opt_b", "label": "Option B (Federated)"},
        ]
        risk_assessments = [
            {"option_key": "opt_a", "risk_score": 0.20, "has_fatal_flaw": False, "risks": ["Latency bottleneck"]},
            {"option_key": "opt_b", "risk_score": 0.65, "has_fatal_flaw": False, "risks": ["Coordination complexity"]},
        ]
        feasibility_assessments = [
            {"option_key": "opt_a", "feasibility_score": 0.85, "benefits": ["Simple governance"]},
            {"option_key": "opt_b", "feasibility_score": 0.45, "benefits": ["Autonomous scaling"]},
        ]

        contract = reviewer.review_debate(
            decision_id="dec-test-01",
            options=options,
            risk_assessments=risk_assessments,
            feasibility_assessments=feasibility_assessments,
        )

        assert contract.verdict == L1ReviewVerdict.SUFFICIENT
        assert contract.deterministic_passed is True
        assert contract.semantic_passed is True
        assert contract.verify_integrity() is True
        assert contract.canonical_digest["value"] is not None

    def test_decision_post_debate_reviewer_detects_missing_assessments(self) -> None:
        reviewer = DecisionPostDebateReviewer()
        options = [
            {"key": "opt_a", "label": "Option A"},
            {"key": "opt_b", "label": "Option B"},
        ]
        # Missing opt_b in risk assessments
        risk_assessments = [
            {"option_key": "opt_a", "risk_score": 0.20, "has_fatal_flaw": False, "risks": ["Risk"]},
        ]
        feasibility_assessments = [
            {"option_key": "opt_a", "feasibility_score": 0.80, "benefits": ["Benefit"]},
            {"option_key": "opt_b", "feasibility_score": 0.70, "benefits": ["Benefit"]},
        ]

        contract = reviewer.review_debate(
            decision_id="dec-test-02",
            options=options,
            risk_assessments=risk_assessments,
            feasibility_assessments=feasibility_assessments,
        )

        assert contract.verdict == L1ReviewVerdict.INSUFFICIENT_REPLAN
        assert contract.deterministic_passed is False
        assert contract.semantic_passed is False
        assert contract.replan_directive is not None
        assert "COMPLETE_OPTION_EVALUATION" in contract.replan_directive["directive_type"]
        assert contract.verify_integrity() is True

    def test_decision_orchestrator_in_flight_l1_review_attached_to_result(self) -> None:
        orchestrator = DecisionOrchestrator()
        options = [
            {"key": "opt_a", "label": "Option A (In-Process Cache)", "description": "Local LRU"},
            {"key": "opt_b", "label": "Option B (Remote Redis)", "description": "Shared cluster"},
        ]
        result = orchestrator.orchestrate_decision(
            decision_id="dec-e2e-review",
            question="Which caching strategy satisfies our sub-5ms requirement?",
            options=options,
            category="architecture",
        )

        assert "l1_post_debate_review" in result
        review = result["l1_post_debate_review"]
        assert review["verdict"] in ("SUFFICIENT", "INSUFFICIENT_REPLAN")
        assert "canonical_digest" in review
        assert review["deterministic_passed"] is True
        assert len(review["deterministic_checks"]) >= 2
