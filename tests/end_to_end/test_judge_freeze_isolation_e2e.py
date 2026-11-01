"""
Wave 2 E2E Test: L4 Judge Deadline Bounding (<=75s) and Per-Artifact Freeze Isolation
across Attempt 2 Surgical Remediation.
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.judges.forensic import _generate_client_forensic
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.eval_cache import resolve_pins_and_cache
from dual_pipeline.transcript_qa_core.frozen_gates import apply_frozen_to_qa_report


def test_forensic_judge_bounds_primary_deadline_to_75s_and_falls_back_e2e() -> None:
    """Verify primary Forensic Judge call uses a 75.0s deadline (not 180.0s) and falls back to medium effort."""
    captured_deadlines = []
    captured_efforts = []

    def _fake_exec(fn, *, deadline_seconds, heartbeat_interval, operation_name):
        captured_deadlines.append((operation_name, deadline_seconds))
        if len(captured_deadlines) == 1:
            raise TimeoutError("Primary high-effort call timed out at 75.0s")
        return fn()

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"composite_score": 95, "verdict": "PASS"}'
    mock_resp.choices = [mock_choice]

    def _create(**kwargs):
        captured_efforts.append(kwargs.get("reasoning_effort"))
        return mock_resp

    mock_client.chat.completions.create.side_effect = _create
    mock_recorder = MagicMock()

    with (
        patch("config.transport.execute_with_deadline_and_heartbeat", side_effect=_fake_exec),
        patch("dual_pipeline.llm_judge_core.telemetry.record_judge_llm_usage"),
    ):
        res = _generate_client_forensic(
            client=mock_client,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            system_prompt="sys",
            user_content="usr",
            recorder=mock_recorder,
            metadata={},
        )

    assert captured_deadlines[0] == ("L4-Judge Forensic Normalization Evaluation", 75.0)
    assert captured_deadlines[1][1] == 90.0
    assert captured_efforts == ["medium"]
    assert res.get("is_fallback") is True
    assert res.get("reasoning_effort") == "medium"


def test_attempt2_output1_remediation_preserves_frozen_output3_without_rejudging_e2e() -> None:
    """Verify deterministic quote re-anchoring on Attempt 2 keeps Output 03 frozen and reuses its pinned scorecard."""
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    f3_attempt1 = '{"metadata": {}, "graph_mutations": {"assertion_proposals": [{"quote": "old"}]}}'
    f3_attempt2_reanchored = '{"metadata": {}, "graph_mutations": {"assertion_proposals": [{"quote": "new"}]}}'

    sm.register_deliverable_pass("output_3", f3_attempt1, score=93)
    assert sm.is_frozen("output_3") is True

    qa_report = {
        "output_01_forensic_transcript": {"status": "PASS", "passed": True, "failures": []},
        "output_02_executive_recap": {"status": "PASS", "passed": True, "failures": []},
        "output_03_canonical_signal_projection": {"status": "PASS", "passed": True, "failures": []},
    }
    apply_frozen_to_qa_report(
        qa_report,
        sm,
        metadata={"state_machine": sm},
        candidate_texts={"output_3": f3_attempt2_reanchored},
    )

    # Output 03 must remain frozen in the state machine and have its frozen content updated to the re-anchored JSON
    assert sm.is_frozen("output_3") is True
    assert sm.get_frozen_content("output_3") == f3_attempt2_reanchored
    assert qa_report["output_03_canonical_signal_projection"]["frozen"] is True

    # Pinned scorecard from Attempt 1 must be aligned to the re-anchored SHA-256 and returned without LLM call
    old_sha = hashlib.sha256(f3_attempt1.encode("utf-8")).hexdigest()
    pinned = {
        "output_3": {
            "composite_score": 93,
            "verdict": "PASS",
            "status": "PASS",
            "artifact_sha256": old_sha,
        }
    }
    with patch("dual_pipeline.transcript_qa_core.eval_cache.check_pin", wraps=lambda p, k1, *a, **kw: p.get(k1)) as spy_pin:
        _, _, out3_pin, _, _, _ = resolve_pins_and_cache(
            f1_text="healed f1",
            f2_text="frozen f2",
            f3_text=f3_attempt2_reanchored,
            model="gpt-5.6-sol",
            reasoning_effort="high",
            pinned=pinned,
            metadata={"state_machine": sm},
        )
        new_sha = hashlib.sha256(f3_attempt2_reanchored.encode("utf-8")).hexdigest()
        assert pinned["output_3"]["artifact_sha256"] == new_sha
        assert out3_pin is not None
        assert out3_pin["composite_score"] == 93
