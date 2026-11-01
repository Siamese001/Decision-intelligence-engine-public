"""Adversarial and Edge-Case Tests for Judge Retry Remediation & Stage 01 Escalation.

Covers:
  - Boundary payloads (empty critique, None reports)
  - Malformed inputs (partial judge reports, missing metadata)
  - Rollback & recovery (fallback escalation to provider)
  - Production regression (Attempt 1 Score 85 organic signal failure on native acoustic transcript)
"""

import pytest
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    check_candidate_loop_termination,
    handle_identical_candidate_fallback,
    compute_candidate_digest,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer


# --- Category 1: Boundary Payloads ---

def test_boundary_payload_empty_and_whitespace_critique():
    """Empty or whitespace critique produces clean non-mutating response."""
    raw = "### [00:00:02]\n\n**Amit Ayer:** Hello team.\n"
    res, did_rem, audit = SurgicalRemediator.remediate_output_1(
        raw,
        qa_report={},
        judge_report={},
        directive="   ",
    )
    assert res == raw
    assert did_rem is False
    assert len(audit) > 0


def test_boundary_payload_empty_transcript():
    """Empty transcript text is handled gracefully without crash."""
    res, did_rem, audit = SurgicalRemediator.remediate_output_1(
        "",
        qa_report={},
        judge_report={},
        directive="ORGANIC SIGNAL DEFECT",
    )
    assert res == ""
    assert did_rem is False


# --- Category 2: Malformed Inputs ---

def test_malformed_judge_report_none_fields():
    """Handles None or malformed judge reports gracefully."""
    raw = "### [00:00:02]\n\n**Amit Ayer:** Good afternoon everybody. I um think to to test.\n"
    res, did_rem, audit = SurgicalRemediator.remediate_output_1(
        raw,
        qa_report={"output_01_forensic_transcript": None},
        judge_report={"output_1_evaluation": None},
        directive="ORGANIC SIGNAL DEFECT: remove fillers and stutters",
    )
    assert did_rem is True
    assert "to test" in res
    assert "to to" not in res
    assert " um " not in res
    assert any("organic signal" in a.lower() for a in audit)


# --- Category 3: Rollback & Recovery / Fallback Escalation ---

def test_fallback_sets_force_stage01_escalation_and_routes_to_provider():
    """Identical candidate fallback sets force_stage01_escalation and routes to provider."""
    mock_strategy = MagicMock()
    mock_result = MagicMock()
    mock_result.output_1 = "# Forensic Transcript\n\n### [00:00:02]\n\n**Amit Ayer:** Regenerated.\n"
    mock_result.output_2 = "Recap"
    mock_result.output_3 = "{}"
    mock_strategy.synthesize.return_value = mock_result

    metadata = {"diarization_status": "DIARIZED_ACOUSTIC_RESOLVED"}
    pa_context = {}

    can_continue, new_res, new_digest = handle_identical_candidate_fallback(
        attempt=2,
        max_attempts=3,
        metadata=metadata,
        pa_context=pa_context,
        synthesis_strategy=mock_strategy,
        source_text="source",
        current_directive="Fix organic signal",
        prior_digest="old_digest",
    )

    assert can_continue is True
    assert metadata.get("force_stage01_escalation") is True
    assert pa_context.get("force_stage01_escalation") is True

    # Now verify ForensicNormalizer routes to provider when force_stage01_escalation is True
    normalizer = ForensicNormalizer()
    mock_provider = MagicMock()
    mock_provider.synthesize_forensic.return_value = "### [00:00:02]\n\n**Amit Ayer:** Provider generated.\n"

    u0_ctx = {"clean_transcript": "raw", "metadata": metadata}
    out = normalizer.execute(
        u0_context=u0_ctx,
        plan=None,
        provider=mock_provider,
        remediation_directive="Fix organic signal",
    )
    mock_provider.synthesize_forensic.assert_called_once()
    assert "Provider generated" in out


# --- Category 4: Production Regression (Megan Orzell / Amit Ayer Score 85 scenario) ---

def test_exact_production_regression_stage01_remediation_alters_candidate_digest():
    """Attempt 2 modifies Output 01 digest when Attempt 1 fails with Score 85 organic signal critique."""
    # Attempt 1 candidate with unpruned fillers, stutters, and passive backchannel
    attempt1_output1 = """# Forensic Transcript

**Date:** 10/16/2026

### [00:00:02]

**Amit Ayer:** Good afternoon, everybody. I um think to to make progress we need AWS.

### [00:00:08]

**Megan Orzell:** Yeah.

### [00:00:10]

**Amit Ayer:** What are the open roles for PartnerAdvisory?

## Ambiguity Lock Register
| # | Category | Timestamp | Speaker | Observed Ambiguity | Resolution |
|---|---|---|---|---|---|
"""
    class Candidate:
        def __init__(self, f1, f2="Recap", f3="{}"):
            self.output_1 = f1
            self.output_2 = f2
            self.output_3 = f3

    attempt1_cand = Candidate(attempt1_output1)
    attempt1_digest = compute_candidate_digest(attempt1_cand)
    assert attempt1_digest is not None

    # Judge report from Attempt 1 failure (Score 85)
    judge_report = {
        "output_1_evaluation": {
            "score": 85,
            "actionable_remediation_diff": "Prune organic signal defect backchannels and repeated word stutters.",
            "critique_diff_envelope": {
                "observed_defect": "ORGANIC SIGNAL DEFECT: Output 01 contains unpruned fillers and backchannels.",
            },
        }
    }
    directive = "ORGANIC SIGNAL DEFECT: prune backchannels and stutters"

    # Run Attempt 2 surgical remediation
    remediated_f1, did_rem, audit = SurgicalRemediator.remediate_output_1(
        attempt1_output1,
        qa_report={},
        judge_report=judge_report,
        directive=directive,
    )

    assert did_rem is True
    # Verify the filler and stutter were cleaned
    assert "to make progress" in remediated_f1
    assert "to to" not in remediated_f1
    assert " um " not in remediated_f1
    # Verify the ungrounded backchannel was pruned
    assert "### [00:00:08]" not in remediated_f1
    assert "Megan Orzell:** Yeah." not in remediated_f1

    # Verify candidate digest CHANGED, preventing pre-judge loop termination
    attempt2_cand = Candidate(remediated_f1)
    attempt2_digest = compute_candidate_digest(attempt2_cand)
    assert attempt2_digest != attempt1_digest

    should_stop, checked_digest = check_candidate_loop_termination(
        attempt=2,
        synthesis_result=attempt2_cand,
        prior_digest=attempt1_digest,
    )
    # The synthesis loop must NOT abort! It must proceed to qa_strategy.evaluate()
    assert should_stop is False
    assert checked_digest == attempt2_digest
