"""
Unit tests for Content-Addressable Evaluation Pinning (Wave 2).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from dual_pipeline.transcript_qa_core.evaluator import (
    evaluate_judge_and_release,
)


def test_pinned_evaluation_skips_forensic_judge_invocation() -> None:
    """Tests that a certified pinned Output 01 evaluation completely bypasses judge_forensic."""
    pinned_o1 = {
        "composite_score": 96,
        "verdict": "PASS",
        "forensic_rationale": "Forensic transcript is fully grounded and attribution clean.",
        "actionable_remediation_diff": "",
        "grounding_citations": ["Citation 1"],
    }

    f1_text = "# Forensic Transcript\n**Date:** 09/10/2026\n[00:01:00] Alex Rivera: Hello."
    f2_text = """# Subject: APEX_TRANS Discovery
**Date:** 09/10/2026
**Attendees:**
- Alex Rivera (APEX)

## Meeting Objective
Test objective.

## Key Discussion Points & Findings
Test points.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Validate scope | Alex Rivera | 09/25/2026 | Confirmed |

## Open Decisions / Blockers
None.

## Business Meaning and Value Case
Test value.

## Executive Decision
No decision.

## Bounded Execution Wedge (Candidate Discovery Hypothesis — Advisor-Proposed)
Hypothesis.
"""
    f3_text = json.dumps({"mutations": {"entities": [], "signals": []}})

    # Patch judge_forensic.evaluate to raise an error if called
    with patch(
        "dual_pipeline.llm_judge_core.forensic.ForensicTranscriptJudge.evaluate",
        side_effect=RuntimeError("FAIL: judge_forensic should not be invoked for pinned artifact!"),
    ):
        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            mock=True,
            pinned_evaluations={"output_1": pinned_o1},
        )

    out1_eval = judge_rep["output_1_evaluation"]
    assert out1_eval["composite_score"] == 96
    assert out1_eval["verdict"] == "PASS"
    assert out1_eval["source"] == "PINNED_CACHE"
    assert final_dec["output_1_llm_score_pass"] is True


def test_content_addressable_cache_reuse() -> None:
    """Tests that byte-identical artifacts are cached by SHA-256 and reused as CONTENT_CACHE."""
    f1 = "# Test Transcript Cache\n**Date:** 09/10/2026\n[00:01] Start"
    f2 = """# Subject: Test Cache
**Date:** 09/10/2026
**Attendees:**
- Anece Elakkari

## Meeting Objective
Objective.

## Key Discussion Points & Findings
Points.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Action item | Anece Elakkari | 09/25/2026 | Complete |

## Open Decisions / Blockers
None.

## Business Meaning and Value Case
Value.

## Executive Decision
No decision.

## Bounded Execution Wedge (Candidate Discovery Hypothesis — Advisor-Proposed)
Wedge.
"""
    f3 = json.dumps({"mutations": {"entities": [], "signals": []}})

    # First evaluation populates _EVALUATION_CACHE
    _, judge_rep1, _ = evaluate_judge_and_release(
        f1_text=f1, f2_text=f2, f3_text=f3, mock=True, model="gpt-5.6-luna", reasoning_effort="high"
    )
    assert judge_rep1["output_1_evaluation"]["composite_score"] >= 90

    # Second evaluation with identical bytes should retrieve from CONTENT_CACHE
    with patch(
        "dual_pipeline.llm_judge_core.forensic.ForensicTranscriptJudge.evaluate",
        side_effect=RuntimeError("FAIL: Cached forensic judge should not be called!"),
    ):
        _, judge_rep2, _ = evaluate_judge_and_release(
            f1_text=f1, f2_text=f2, f3_text=f3, mock=True, model="gpt-5.6-luna", reasoning_effort="high"
        )
        assert judge_rep2["output_1_evaluation"]["source"] == "CONTENT_CACHE"


def test_non_passing_pin_is_rejected_and_evaluated() -> None:
    """Tests that a pinned evaluation below the required threshold (<90) is ignored."""
    low_pin = {
        "composite_score": 75,
        "verdict": "FAIL",
    }
    f1 = "# Low Pin Transcript\n**Date:** 09/10/2026\n[00:01] Start"
    f2 = """# Subject: Test Low Pin
**Date:** 09/10/2026
**Attendees:**
- Peter Ashley

## Meeting Objective
Objective.

## Key Discussion Points & Findings
Points.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Action item | Peter Ashley | 09/25/2026 | Complete |

## Open Decisions / Blockers
None.

## Business Meaning and Value Case
Value.

## Executive Decision
No decision.

## Bounded Execution Wedge (Candidate Discovery Hypothesis — Advisor-Proposed)
Wedge.
"""
    f3 = json.dumps({"mutations": {"entities": [], "signals": []}})

    # Should NOT use low_pin (75); mock evaluator will run and produce score 96
    _, judge_rep, _ = evaluate_judge_and_release(
        f1_text=f1, f2_text=f2, f3_text=f3, mock=True, pinned_evaluations={"output_1": low_pin}
    )
    assert judge_rep["output_1_evaluation"]["composite_score"] >= 90
    assert judge_rep["output_1_evaluation"].get("source") != "PINNED_CACHE"
