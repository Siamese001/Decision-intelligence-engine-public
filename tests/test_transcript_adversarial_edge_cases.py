"""
tests/test_transcript_adversarial_edge_cases.py

Adversarial and edge-case tests for transcript cross-output synchronization:
1. Boundary: Empty evidence index, 0-length dialogue, single-word turns.
2. Malformed: Inverted quote pointers [end < start], non-numeric locators, missing exact_text.
3. Invariant Bypass: Cross-speaker quote attribution attempts and out-of-bounds offsets.
4. Recovery: Graceful fallback when no speaker or turn matches the locator.
"""
from __future__ import annotations

import concurrent.futures
import json
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from dual_pipeline.transcript_stages_core.x_gates import (
    XExitGatesEvaluator,
    _find_best_verbatim_span,
    _parse_transcript_turns,
)


def test_boundary_empty_and_minimal_inputs():
    """Boundary: Empty evidence index, empty transcript, and zero-length dialogue must not fail."""
    evaluator = XExitGatesEvaluator()

    # 1. Empty data
    empty_data: Dict[str, Any] = {"evidence_index": {}}
    evaluator._heal_quote_pointers(empty_data, transcript="")
    assert empty_data["evidence_index"] == {}

    # 2. Non-dict evidence_index
    corrupt_data: Dict[str, Any] = {"evidence_index": "not a dict"}
    evaluator._heal_quote_pointers(corrupt_data, transcript="Alice: hello")
    assert corrupt_data["evidence_index"] == "not a dict"

    # 3. Empty turn parsing
    turns = _parse_transcript_turns("")
    assert turns == []


def test_malformed_inverted_and_invalid_quote_pointers():
    """Malformed: Inverted quote pointers [end < start] or non-numeric offsets are safely normalized."""
    f1_text = "### [00:01:00]\n\n**Alice:** Reliable platform engineering enables autonomous velocity."

    f3_data = {
        "evidence_index": {
            "ev-inv": {
                "statement_id": "ev-inv",
                "speaker": "person:alice",
                "text": "Reliable platform engineering enables autonomous velocity.",
                "exact_text": "Reliable platform engineering enables autonomous velocity.",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
                "quote_pointer": [500, 10],  # Inverted!
            },
            "ev-str": {
                "statement_id": "ev-str",
                "speaker": "person:alice",
                "text": "Reliable platform engineering",
                "exact_text": "Reliable platform engineering",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
                "quote_pointer": {"start_offset": "invalid", "end_offset": 50},  # Non-integer start
            },
        }
    }

    evaluator = XExitGatesEvaluator()
    evaluator._heal_quote_pointers(f3_data, transcript=f1_text)

    # ev-inv should be re-anchored to valid positive offsets in f1_text
    qp_inv = f3_data["evidence_index"]["ev-inv"]["quote_pointer"]
    assert qp_inv[0] < qp_inv[1]
    assert f1_text[qp_inv[0]:qp_inv[1]] == "Reliable platform engineering enables autonomous velocity."


def test_invariant_bypass_cross_speaker_quote_attribution():
    """Invariant bypass: Quote pointer healer must strictly match speaker and timestamp rather than cross-contaminating."""
    f1_text = (
        "### [00:02:00]\n\n"
        "**Bob:** We should decommission the legacy monolith right away.\n\n"
        "### [00:04:00]\n\n"
        "**Alice:** We should prioritize the cloud migration before decommissioning anything."
    )

    f3_data = {
        "evidence_index": {
            "ev-alice": {
                "statement_id": "ev-alice",
                "speaker": "person:alice",
                "text": "We should prioritize the cloud migration before decommissioning.",
                "exact_text": "We should prioritize the cloud migration before decommissioning.",
                "locator": {"kind": "timestamp", "value": "00:04:00"},
                "quote_pointer": [0, 10],
            }
        }
    }

    evaluator = XExitGatesEvaluator()
    evaluator._heal_quote_pointers(f3_data, transcript=f1_text)

    ev = f3_data["evidence_index"]["ev-alice"]
    qp = ev["quote_pointer"]
    reanchored = ev["exact_text"]

    # Must re-anchor to Alice's turn at 00:04:00, NOT Bob's turn at 00:02:00
    assert "Alice" not in reanchored
    assert "prioritize the cloud migration" in reanchored
    assert f1_text[qp[0]:qp[1]] == reanchored


def test_recovery_graceful_fallback_when_no_turn_matches():
    """Rollback & recovery: When no speaker or turn matches the evidence item, healer safely preserves content."""
    f1_text = "### [00:01:00]\n\n**Dave:** Completely unrelated discussion about network switches."

    f3_data = {
        "evidence_index": {
            "ev-lost": {
                "statement_id": "ev-lost",
                "speaker": "person:charlie",
                "text": "Quantum computing cryptographic key exchange protocol.",
                "exact_text": "Quantum computing cryptographic key exchange protocol.",
                "locator": {"kind": "timestamp", "value": "00:55:00"},
                "quote_pointer": [100, 155],
            }
        }
    }

    evaluator = XExitGatesEvaluator()
    # Must not raise an exception even with zero overlap
    evaluator._heal_quote_pointers(f3_data, transcript=f1_text)

    # Content is preserved cleanly
    ev = f3_data["evidence_index"]["ev-lost"]
    assert "Quantum computing" in ev["exact_text"]


def test_verbatim_span_minimum_length_cutoff():
    """Boundary: Verbatim span extraction must reject snippets below 15 characters."""
    dialogue = "Yes. Ok. Sure thing."
    # Snippet with only 3 characters matching
    span = _find_best_verbatim_span(dialogue, "Yes.", min_chars=15)
    assert span is None or len(span) >= 15


# -----------------------------------------------------------------------------
# Wave 2 Adversarial Fault-Injection: Micro-Judge Timeout & Scorecard Conformance
# -----------------------------------------------------------------------------

class _MockCompletionResponse:
    def __init__(self, content: str):
        self.choices = [MagicMock(message=MagicMock(content=content))]
        self.usage = MagicMock(prompt_tokens=100, completion_tokens=50)


def test_adversarial_judge_primary_timeout_effort_downgrade_recovery():
    """Adversarial recovery: When primary reasoning_effort='high' judge stalls/times out,

    ForensicTranscriptJudge automatically retries once with reasoning_effort='medium'
    and recovers within the deadline bounds.
    """
    import concurrent.futures
    from unittest.mock import MagicMock
    from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import Output1ScorecardV2

    raw_source = (
        "### [00:01:00]\n\n"
        "**Speaker 1:** We need to verify that all database connections initialize in WAL mode.\n\n"
        "### [00:02:00]\n\n"
        "**Speaker 2:** Agreed, that prevents write lock contention across threads."
    )
    f1_text = (
        "### [00:01:00]\n\n"
        "**Alice:** We need to verify that all database connections initialize in WAL mode.\n\n"
        "### [00:02:00]\n\n"
        "**Bob:** Agreed, that prevents write lock contention across threads.\n\n"
        "### Ambiguity Lock Register\n\n"
        "| Locator | Raw Entity / Speaker | Authoritative Resolution | Confidence | Resolution Status | Evidentiary Basis |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| [00:01:00] | Speaker 1 | Alice | HIGH | RESOLVED | Roster check |\n"
        "| [00:02:00] | Speaker 2 | Bob | HIGH | RESOLVED | Roster check |"
    )

    calls = []

    def mock_create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("reasoning_effort") == "high":
            raise TimeoutError("Primary high-effort judge call timed out")

        valid_scorecard = json.dumps({
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 96.0,
            "dimension_1_forensic_grounding_and_fidelity": 24.0,
            "dimension_2_attribution_and_room_mic_hygiene": 24.0,
            "dimension_3_chronological_and_timestamp_integrity": 24.0,
            "dimension_4_asr_cleanup_and_domain_preservation": 24.0,
            "forensic_rationale": "Forensic normalization satisfies all dimensions on retry.",
            "evaluated_span_coverage": 1.0,
            "defect_codes": [],
            "actionable_remediation_diff": "",
        })
        return _MockCompletionResponse(valid_scorecard)

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create

    judge = ForensicTranscriptJudge(model="gpt-5.6-sol", reasoning_effort="high", use_local_model=False)
    result = judge.evaluate(
        f1_text=f1_text,
        raw_source_text=raw_source,
        client=mock_client,
        mock=False,
        metadata={"forensic_judge_timeout_s": 5.0, "forensic_judge_retry_timeout_s": 5.0},
    )

    # Verify both attempts ran with expected reasoning effort progression
    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[1]["reasoning_effort"] == "medium"

    # Verify recovery and scorecard provenance
    assert result["verdict"] in ("PASS", "PASS_ADVISORY")
    assert result["composite_score"] >= 90.0
    assert result["reasoning_effort"] == "medium"
    assert result["is_fallback"] is True

    # Independently verify against Output1ScorecardV2 schema
    card = Output1ScorecardV2.model_validate(result)
    assert card.verdict in ("PASS", "PASS_ADVISORY")
    assert card.composite_score >= 90.0


def test_adversarial_judge_double_timeout_emits_v2_conforming_scorecard():
    """Adversarial failure: When both primary and medium-effort retry time out,

    ForensicTranscriptJudge emits a schema-conforming failing scorecard with ERR_JUDGE_TIMEOUT.
    """
    import concurrent.futures
    from unittest.mock import MagicMock
    from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import Output1ScorecardV2

    raw_source = "### [00:01:00]\n\n**Speaker 1:** Testing double timeout stall."
    f1_text = (
        "### [00:01:00]\n\n**Alice:** Testing double timeout stall.\n\n"
        "### Ambiguity Lock Register\n\n"
        "| Locator | Raw Entity / Speaker | Authoritative Resolution | Confidence | Resolution Status | Evidentiary Basis |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| [00:01:00] | Speaker 1 | Alice | HIGH | RESOLVED | Roster check |"
    )

    calls = []

    def mock_create_stall(**kwargs):
        calls.append(kwargs)
        raise TimeoutError(f"Stall simulated at reasoning_effort={kwargs.get('reasoning_effort')}")

    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create_stall

    judge = ForensicTranscriptJudge(model="gpt-5.6-sol", reasoning_effort="high", use_local_model=False)
    result = judge.evaluate(
        f1_text=f1_text,
        raw_source_text=raw_source,
        client=mock_client,
        mock=False,
        metadata={"forensic_judge_timeout_s": 5.0, "forensic_judge_retry_timeout_s": 5.0},
    )

    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[1]["reasoning_effort"] == "medium"

    assert result["verdict"] == "FAIL"
    assert result["composite_score"] == 0
    assert result["defect_codes"] == ["ERR_JUDGE_TIMEOUT"]

    card = Output1ScorecardV2.model_validate(result)
    assert card.verdict == "FAIL"
    assert card.composite_score == 0.0
    assert card.defect_codes == ["ERR_JUDGE_TIMEOUT"]


def test_adversarial_await_micro_judge_timeout_payload_conforms_to_v2():
    """Adversarial timeout: When _await_micro_judge times out, the fallback payload

    conforms fully to Output1ScorecardV2 schema and passes release policy validation.
    """
    import concurrent.futures
    from unittest.mock import MagicMock
    from dual_pipeline.prompts.contracts.versioned_judge_contracts import Output1ScorecardV2
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    from dual_pipeline.transcript_qa_core.evaluator import _await_micro_judge

    mock_timeout_future = MagicMock()
    mock_timeout_future.result.side_effect = concurrent.futures.TimeoutError("Thread pool timeout")

    # 1. Output 1 timeout fallback
    out1_fallback = _await_micro_judge(mock_timeout_future, key="out1", out_num=1)
    assert out1_fallback["status"] == "FAIL"
    assert out1_fallback["verdict"] == "FAIL"
    assert out1_fallback["failure_type"] == "TIMEOUT"
    assert out1_fallback["composite_score"] == 0.0
    assert out1_fallback["defect_codes"] == ["ERR_JUDGE_TIMEOUT"]

    # Validate against Output1ScorecardV2 pydantic model
    card1 = Output1ScorecardV2.model_validate(out1_fallback)
    assert card1.verdict == "FAIL"
    assert card1.composite_score == 0.0
    assert card1.defect_codes == ["ERR_JUDGE_TIMEOUT"]
    assert card1.dimension_1_forensic_grounding_and_fidelity == 0.0

    # Validate against release policy
    ok1, errs1 = validate_micro_judge_scorecard(out1_fallback, output_num=1)
    assert ok1 is True, f"Output 1 timeout fallback rejected: {errs1}"

    # 2. Output 2 timeout fallback
    out2_fallback = _await_micro_judge(mock_timeout_future, key="out2", out_num=2)
    assert out2_fallback["verdict"] == "FAIL"
    assert "section_scores" in out2_fallback
    ok2, errs2 = validate_micro_judge_scorecard(out2_fallback, output_num=2)
    assert ok2 is True, f"Output 2 timeout fallback rejected: {errs2}"

    # 3. Output 3 timeout fallback
    out3_fallback = _await_micro_judge(mock_timeout_future, key="out3", out_num=3)
    assert out3_fallback["verdict"] == "FAIL"
    assert "dimension_1_strategic_edge" in out3_fallback
    ok3, errs3 = validate_micro_judge_scorecard(out3_fallback, output_num=3)
    assert ok3 is True, f"Output 3 timeout fallback rejected: {errs3}"
