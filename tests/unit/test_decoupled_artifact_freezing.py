"""
tests/unit/test_decoupled_artifact_freezing.py

Unit tests for Wave 1: Decoupled Monotonic Artifact Freezing & Checkpoint Sealing.
Verifies:
1. validate_frozen_artifact_gate succeeds when Output 02 passed QA in an attempt subfolder even if Output 03 failed.
2. Selective regeneration (--regenerate 03) freezes Output 01 and 02 from attempts.
3. Monotonic preservation: subsequent attempt failure in sibling stage cannot toggle passing deliverable to failed.
4. Checkpoint memoizer recognizes stage-isolated seals (output_1_seal, output_2_seal, output_3_seal).
5. State machine register_deliverable_pass permanently seals artifacts and persists stage seals.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _is_decision_passing,
    _is_decision_valid_and_matching,
    memoize_substage_checkpoints,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.remediation_core.artifact_validator import (
    validate_frozen_artifact_gate,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


def test_validate_frozen_artifact_gate_passes_from_attempt_subfolder():
    """Verify Output 02 passes validation gate when Output 02 passed in attempt_2 even though Output 03 failed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        att_dir = out_p / "attempts" / "attempt_2"
        att_dir.mkdir(parents=True, exist_ok=True)

        recap_content = (
            "# Executive Recap\n\n"
            "## Meeting Objective\nDiscuss architecture.\n\n"
            "## Key Discussion Points & Findings\nFoundations solid.\n\n"
            "## Action & Governance Ledger\n"
            "| Item | Action | Owner | Target Date | Status |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Refactor | Alice | 10-15-2026 | OPEN |\n"
        )
        recap_file = att_dir / "02_test_meeting_10-02-2026_executive_recap.md"
        recap_file.write_text(recap_content, encoding="utf-8")
        recap_sha = hashlib.sha256(recap_content.encode("utf-8")).hexdigest()

        # Attempt 2 decision: Output 2 passed deterministic gates, but Output 3 failed, causing short-circuit
        att_dec = {
            "attempt": 2,
            "max_attempts": 3,
            "output_1_deterministic_gates_pass": True,
            "output_1_llm_score_pass": True,
            "output_1_pass": True,
            "output_2_deterministic_gates_pass": True,
            "output_2_llm_score_pass": False,
            "output_2_skipped": True,
            "output_2_pass": False,
            "output_3_deterministic_gates_pass": False,
            "output_3_llm_score_pass": False,
            "output_3_pass": False,
            "short_circuit": True,
            "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
            "status": "FAIL",
            "artifact_manifest": {
                "output_2_sha256": recap_sha,
                "decision_seal_sha256": "mock_seal",
            },
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(att_dec), encoding="utf-8")

        # QA report in attempt 2 confirms output_02 passed all gates
        qa_rep = {
            "output_02_executive_recap": {"passed": True, "failures": []},
            "output_03_canonical_signal_projection": {"passed": False, "failures": ["Gate 3.14 failed"]},
        }
        (att_dir / "qa_gates_report.json").write_text(json.dumps(qa_rep), encoding="utf-8")

        # Root decision reports overall status FAIL
        root_dec = {"status": "FAIL", "final_pass": False}
        (out_p / "final_release_decision.json").write_text(json.dumps(root_dec), encoding="utf-8")

        metadata = {"synthesis_prompt_version": "V5_7"}
        res = validate_frozen_artifact_gate(out_p, "output_2", metadata)

        assert res.valid is True
        assert res.artifact_key == "output_2"
        assert res.content is not None
        assert "Executive Recap" in res.content
        assert res.reason == "OK"


def test_selective_regeneration_seeds_passing_deliverables():
    """Verify SelectiveRemediationStateMachine freezes Output 01 and 02 when regenerating only 03."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        att_dir = out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)

        transcript_content = (
            "# Forensic Transcript\n\n"
            "**Alice**: We need to decouple the artifact validator.\n"
            "**Bob**: Agreed, monotonic sealing prevents invalidation."
        )
        recap_content = (
            "# Executive Recap\n\n"
            "## Meeting Objective\nDecouple stages.\n\n"
            "## Action & Governance Ledger\n"
            "| Item | Action | Owner | Target Date | Status |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Ship fix | Alice | 10-15-2026 | OPEN |\n"
        )

        (att_dir / "01_test_meeting_10-02-2026_hitl_resolved_transcript.md").write_text(
            transcript_content, encoding="utf-8"
        )
        (att_dir / "02_test_meeting_10-02-2026_executive_recap.md").write_text(
            recap_content, encoding="utf-8"
        )

        t_sha = hashlib.sha256(transcript_content.encode("utf-8")).hexdigest()
        r_sha = hashlib.sha256(recap_content.encode("utf-8")).hexdigest()

        dec = {
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": False,
            "status": "FAIL",
            "artifact_manifest": {
                "output_1_sha256": t_sha,
                "output_2_sha256": r_sha,
            },
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        metadata = {"attendees": ["Alice", "Bob"], "synthesis_prompt_version": "V5_7"}

        results = sm.seed_for_selective_regeneration(out_p, "03", metadata=metadata)
        assert "output_1" in results
        assert "output_2" in results
        assert results["output_1"].valid is True
        assert results["output_2"].valid is True

        assert sm.is_frozen("output_1") is True
        assert sm.is_frozen("output_2") is True
        assert sm.is_frozen("output_3") is False
        assert "output_1" in sm.permanently_frozen
        assert "output_2" in sm.permanently_frozen


def test_monotonic_pass_preservation_across_attempts():
    """Verify that once an artifact passes, sibling stage failure on later attempt cannot toggle it to failed."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    recap_text = "# Executive Recap\nDecoupled velocity passed."
    sm.register_deliverable_pass("output_2", recap_text, score=95)

    assert sm.is_frozen("output_2") is True
    assert "output_2" in sm.permanently_frozen
    assert "output_2" in sm.passing_stages

    # Attempt 2: Output 3 fails completely
    judge_report = {
        "output_1_evaluation": {"composite_score": 90, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 0, "verdict": "FAIL"},  # e.g. skipped or short-circuited
        "output_3_evaluation": {"composite_score": 40, "verdict": "FAIL"},
        "cross_artifact_reconciliation": {"passed": False},
    }
    qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": False},
    }
    final_decision = {"status": "FAIL"}

    artifacts = {
        "output_1": "# Forensic Transcript\n**Alice**: hi",
        "output_2": recap_text,
        "output_3": '{"entities": []}',
    }

    all_passed, failed_stages = sm.evaluate_attempt(
        final_decision, judge_report, qa_report, artifacts=artifacts, is_notes_mode=False
    )

    assert all_passed is False
    assert "output_3" in failed_stages
    assert "output_2" not in failed_stages  # Output 02 preserved!
    assert sm.is_frozen("output_2") is True  # Output 02 remains frozen!
    assert sm.get_frozen_content("output_2") == recap_text


def test_checkpoint_memoizer_stage_isolated_seals():
    """Verify checkpoint memoizer recognizes stage-isolated seals (output_1_seal, output_2_seal, output_3_seal)."""
    # 1. Output 2 seal in decision dict
    dec_out2 = {
        "output_2_deterministic_gates_pass": True,
        "output_2_seal": True,
        "output_2_sha256": "abc123sha",
    }
    assert _is_decision_passing(dec_out2, "output_2_pass") is True
    assert _is_decision_valid_and_matching(dec_out2, "output_2_pass") is True

    # 2. Output 3 seal in decision dict
    dec_out3 = {
        "output_3_pass": True,
        "output_3_seal": True,
    }
    assert _is_decision_passing(dec_out3, "output_3_pass") is True
    assert _is_decision_valid_and_matching(dec_out3, "output_3_pass") is True

    # 3. Output 1 seal in artifact_manifest
    dec_out1 = {
        "output_1_pass": True,
        "artifact_manifest": {
            "output_1_seal_sha256": "seal_hash_1",
        },
    }
    assert _is_decision_passing(dec_out1, "output_1_pass") is True
    assert _is_decision_valid_and_matching(dec_out1, "output_1_pass") is True

    # 4. Reject unsealed decision
    dec_unsealed = {
        "output_2_pass": True,
    }
    assert _is_decision_passing(dec_unsealed, "output_2_pass") is True
    assert _is_decision_valid_and_matching(dec_unsealed, "output_2_pass") is False


def test_checkpoint_memoizer_memoize_substage_checkpoints_from_attempt():
    """Verify memoize_substage_checkpoints reads passing checkpoints from attempt subfolder with isolated seals."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_p = Path(tmp_dir)
        att_dir = out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)

        recap_content = (
            "# Executive Recap\n\n"
            "## Meeting Objective\n"
            "Detailed architectural review of the decoupled pipeline velocity and artifact freezing system.\n\n"
            "## Key Discussion Points & Findings\n"
            "The 15-Agent Council identified that cascade invalidations cause futile re-syntheses. Decoupled freezing seals artifacts independently.\n\n"
            "## Action & Governance Ledger\n"
            "| Item | Action | Owner | Target Date | Status |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Seal deliverables independently | Alice | 10-15-2026 | COMPLETE |\n"
        )
        (att_dir / "output_2.ckpt.md").write_text(recap_content, encoding="utf-8")

        # Stage seals file in attempt directory
        stage_seals = {
            "output_2_seal": True,
            "output_2_pass": True,
            "output_2_sha256": hashlib.sha256(recap_content.encode("utf-8")).hexdigest(),
        }
        (att_dir / "stage_seals.json").write_text(json.dumps(stage_seals), encoding="utf-8")
        (att_dir / "final_release_decision.json").write_text(
            json.dumps({"status": "FAIL", "output_2_deterministic_gates_pass": True, "output_2_seal": True}),
            encoding="utf-8",
        )

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        metadata = {}

        memoized = memoize_substage_checkpoints(out_p, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

        assert memoized["output_2"] is True
        assert sm.is_frozen("output_2") is True
        assert sm.get_frozen_content("output_2") == recap_content
        assert "output_2" in sm.permanently_frozen


def test_register_deliverable_pass_persists_stage_seals():
    """Verify register_deliverable_pass writes isolated checkpoint file and stage_seals.json."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        ckpt_dir = Path(tmp_dir) / "attempts" / "attempt_1"
        sm = SelectiveRemediationStateMachine(max_attempts=3)

        content = "# Forensic Transcript\n**Alice**: Sealed content"
        sm.register_deliverable_pass("output_1", content, score=98, checkpoint_dir=ckpt_dir)

        assert (ckpt_dir / "output_1.ckpt.md").is_file()
        assert (ckpt_dir / "output_1.ckpt.md").read_text(encoding="utf-8") == content

        seals_file = ckpt_dir / "stage_seals.json"
        assert seals_file.is_file()
        seals = json.loads(seals_file.read_text(encoding="utf-8"))
        assert seals.get("output_1_seal") is True
        assert seals.get("output_1_pass") is True
        assert seals.get("output_1_sha256") == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_stage02_synthesis_bypassed_when_frozen():
    """Verify Stage 02 synthesis span is 0.0s and bypasses LLM call when frozen in state machine."""
    import time
    from unittest.mock import MagicMock
    from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy

    strategy = FullMeetingSynthesisStrategy(provider=MagicMock())
    mock_l2_recap = MagicMock()
    mock_l2_recap.execute.side_effect = AssertionError("LLM Stage 02 must not be invoked when frozen!")
    strategy.l2_recap = mock_l2_recap

    sm = SelectiveRemediationStateMachine(max_attempts=3)
    frozen_recap = "# Executive Recap\nPreserved frozen text."
    sm.register_deliverable_pass("output_2", frozen_recap)

    t0 = time.perf_counter()
    res = strategy._resolve_output_2(
        forensic_transcript="transcript text",
        pa_context={"metadata": {"state_machine": sm}},
        attempt=1,
        remediation_directive=None,
        progress_notify=None,
        ckpt_dir=None,
    )
    elapsed = time.perf_counter() - t0

    assert res == frozen_recap
    assert elapsed < 0.05  # sub-50ms deterministic return
    mock_l2_recap.execute.assert_not_called()
