"""
tests/test_decomposed_judge_fast_path.py

Regression test suite validating Wave 4:
1. Decomposed multi-micro-judge parallel execution under gpt-5.6-sol.
2. Concurrent evaluation of Output 1, Output 2, Output 3, and cross-artifact reconciliation.
3. Fast-path latency verification and composite verdict resolution.
"""
from __future__ import annotations

import json
import time

from dual_pipeline.llm_judge import DecomposedMultiJudge
from dual_pipeline.transcript_qa import evaluate_judge_and_release

SAMPLE_F1 = (
    "# Meeting Transcript\n\n"
    "**Date:** 09/18/2026\n\n"
    "### [00:00:10] -- stmt-001\n\n"
    "**Amit Ayer (PartnerAdvisory):** We are reviewing the architecture today.\n\n"
    "### [00:01:00] -- stmt-002\n\n"
    "**Jake Stark (PartnerAdvisory):** The deployment is progressing according to schedule.\n\n"
    "---\n\n"
    "## Ambiguity Lock Register\n\n"
    "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
    "|---|---|---|---|---|---|\n"
    "| 1 | Speaker Attribution & Acoustic Grounding | [00:00:10] | stmt-001 | Amit Ayer (PartnerAdvisory) confirmed | Roster Confirmed |\n"
)

SAMPLE_F2 = (
    "# Subject: Architecture Sync -- Executive Brief\n\n"
    "**Date:** 09/18/2026\n\n"
    "# Attendees\n"
    "- Amit Ayer (PartnerAdvisory)\n"
    "- Jake Stark (PartnerAdvisory)\n\n"
    "# Meeting Objective\n"
    "Review architecture and verify pipeline deployment trajectory.\n\n"
    "# Key Discussion Points & Findings\n"
    "The deployment trajectory is currently within planned limits.\n\n"
    "# Recommendations & Decisions Required\n"
    "No formal action items were assigned during this session; open initiatives remain unresolved questions.\n\n"
    "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
    "### Amit Ayer - PartnerAdvisory\n"
    "Reviewed architecture.\n"
)

SAMPLE_F3 = json.dumps({
    "schema_version": "5.7",
    "meeting_id": "test_meeting",
    "graph_mutations": {
        "entity_upserts": [
            {"id": "person:amit-ayer", "type": "person", "mutation": "CONFIRM"},
            {"id": "person:jake-stark", "type": "person", "mutation": "CONFIRM"},
        ],
        "relationship_signals": [],
        "decision_updates": [],
        "proposal_updates": [],
        "action_updates": [],
        "risk_updates": [],
    },
})


def test_decomposed_judge_fast_path_mock():
    """Verify DecomposedMultiJudge executes mock evaluations concurrently in sub-second time."""
    start = time.monotonic()
    judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="medium", mock=True)
    report = judge.evaluate_parallel(
        output_1_text=SAMPLE_F1,
        output_2_text=SAMPLE_F2,
        output_3_text=SAMPLE_F3,
        metadata={"date": "09/18/2026", "meeting_name": "Architecture Sync"},
    )
    duration = time.monotonic() - start

    assert duration < 2.0  # Concurrent mock execution should finish rapidly
    assert report["status"] == "PASS"
    assert report["evaluation_mode"] == "DECOMPOSED_CONCURRENT"
    assert report["output_1_evaluation"]["composite_score"] >= 95
    assert report["output_2_evaluation"]["composite_score"] >= 95
    assert report["output_3_evaluation"]["composite_score"] >= 95
    assert report["cross_artifact_reconciliation"]["status"] == "PASS"


def test_evaluate_judge_and_release_fast_path():
    """Verify evaluate_judge_and_release orchestrates deterministic QA, micro-judges, and release seal."""
    qa_report, judge_report, final_decision = evaluate_judge_and_release(
        f1_text=SAMPLE_F1,
        f2_text=SAMPLE_F2,
        f3_text=SAMPLE_F3,
        metadata={"date": "09/18/2026", "meeting_name": "Architecture Sync"},
        mock=True,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        auto_heal=True,
    )

    assert judge_report["evaluator_model"] == "gpt-5.6-sol"
    assert "artifact_manifest" in final_decision
    assert final_decision["artifact_manifest"]["evaluator_model"] == "gpt-5.6-sol"
