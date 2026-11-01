"""
tests/test_downstream_retry_isolation.py

Verifies decoupled downstream retries and selective artifact freezing/unfreezing in qa_runner.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.qa_runner import (
    _freeze_passing_outputs,
    _handle_remediation,
)


def test_selective_freezing_preserves_isolated_passing_stages():
    sm = SelectiveRemediationStateMachine()
    final_decision = {
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": False,
        "status": "FAIL",
    }
    _freeze_passing_outputs(
        state_machine=sm,
        final_decision=final_decision,
        forensic_transcript="f1_content",
        executive_recap="f2_content",
        graph_json="f3_content",
    )

    assert sm.is_frozen("output_1")
    assert sm.is_frozen("output_2")
    assert not sm.is_frozen("output_3")


def test_handle_remediation_unfreezes_only_failing_stage():
    sm = SelectiveRemediationStateMachine()
    sm.freeze_artifact("output_1", "f1_content")
    sm.freeze_artifact("output_2", "f2_content")
    sm.freeze_artifact("output_3", "f3_content")

    final_decision = {
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": False,
        "status": "FAIL",
    }
    meta = {}
    tracker = MagicMock()

    _handle_remediation(
        attempt=1,
        tracker=tracker,
        metadata=meta,
        is_notes_mode=False,
        qa_report={},
        judge_report={},
        final_decision=final_decision,
        forensic_transcript="f1_content",
        executive_recap="f2_content",
        graph_json="f3_content",
        state_machine=sm,
    )

    # Output 1 and 2 MUST remain frozen, Output 3 must be unfrozen
    assert sm.is_frozen("output_1")
    assert sm.is_frozen("output_2")
    assert not sm.is_frozen("output_3")
    assert "remediation_directive_graph" in meta
    assert "remediation_directive_recap" not in meta
    assert "remediation_directive_forensic" not in meta


def test_short_circuit_skipped_judge_preserves_frozen_output1_and_3():
    sm = SelectiveRemediationStateMachine()
    sm.freeze_artifact("output_1", "f1_content")
    sm.freeze_artifact("output_2", "f2_content")
    sm.freeze_artifact("output_3", "f3_content")

    # Simulates build_short_circuit_eval where Output 2 failed Gate 2.1
    final_decision = {
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": False,
        "output_1_pass": False,
        "output_2_deterministic_gates_pass": False,
        "output_2_llm_score_pass": False,
        "output_2_pass": False,
        "output_3_deterministic_gates_pass": True,
        "output_3_llm_score_pass": False,
        "output_3_pass": False,
        "status": "FAIL",
    }
    judge_report = {
        "evaluation_mode": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "output_1_evaluation": {"status": "SKIPPED"},
        "output_2_evaluation": {"status": "SKIPPED"},
        "output_3_evaluation": {"status": "SKIPPED"},
    }
    qa_report = {
        "output_02_executive_recap": {"failures": ["Output 2 missing required section: 'Action & Governance Ledger'"]},
    }
    meta = {}
    tracker = MagicMock()

    _handle_remediation(
        attempt=1,
        tracker=tracker,
        metadata=meta,
        is_notes_mode=False,
        qa_report=qa_report,
        judge_report=judge_report,
        final_decision=final_decision,
        forensic_transcript="f1_content",
        executive_recap="f2_content",
        graph_json="f3_content",
        state_machine=sm,
    )

    # Output 1 and Output 3 MUST remain frozen despite output_X_pass=False because their
    # deterministic gates passed and LLM judge was merely SKIPPED due to Output 2's failure.
    assert sm.is_frozen("output_1")
    assert sm.is_frozen("output_3")
    assert not sm.is_frozen("output_2")
    assert "remediation_directive_recap" in meta
    assert "remediation_directive_forensic" not in meta
    assert "remediation_directive_graph" not in meta


def test_is_deliberate_reset_distinguishes_recovery_and_regeneration(tmp_path):
    from dual_pipeline.canonical_pipeline.attachments import is_deliberate_reset, try_resume_passing_attempt
    import json

    assert is_deliberate_reset({"force_rerun": True}) is True
    assert is_deliberate_reset({"force_rerun": True, "recovery": True}) is False
    assert is_deliberate_reset({"force_rerun": True, "resume": True}) is False
    assert is_deliberate_reset({"force_rerun": True, "regenerate": "02,03"}) is False
    assert is_deliberate_reset({"force_rerun": False}) is False
    assert is_deliberate_reset({}) is False

    # Verify try_resume_passing_attempt respects recovery over force_rerun
    att_dir = tmp_path / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True)
    dec = {"authoritative_write_authorized": True, "status": "PASS"}
    (att_dir / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
    (att_dir / "output_1.ckpt.md").write_text("transcript", encoding="utf-8")
    (att_dir / "output_2.ckpt.md").write_text("recap", encoding="utf-8")
    (att_dir / "output_3.ckpt.json").write_text("{}", encoding="utf-8")

    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

    # Deliberate reset rejects resume
    assert try_resume_passing_attempt(tmp_path, {"force_rerun": True}, CanonicalModality.MEETING_TRANSCRIPT) is None

    # Recovery with static force_rerun allows resume
    resumed = try_resume_passing_attempt(tmp_path, {"force_rerun": True, "resume": True}, CanonicalModality.MEETING_TRANSCRIPT)
    assert resumed is not None
    attempt, s_res, e_res = resumed
    assert attempt == 1
    assert e_res.passed is True


def test_unchanged_candidate_terminates_synthesis_loop_early(monkeypatch, tmp_path):
    from dual_pipeline.canonical_pipeline.lifecycle import run_synthesis_loop
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult

    call_counts = {"synthesize": 0, "evaluate": 0}
    fixed_synth = SynthesisResult(
        output_1="static_transcript",
        output_2="static_recap",
        output_3='{"graph_mutations": {"entities": []}}',
        attempt=1,
    )

    def mock_synthesize(*args, **kwargs):
        call_counts["synthesize"] += 1
        return fixed_synth

    def mock_evaluate(*args, **kwargs):
        call_counts["evaluate"] += 1
        return QAEvaluationResult(
            passed=False,
            qa_report={"gate": "failed"},
            judge_report={"status": "FAIL"},
            final_decision={"status": "FAIL"},
            authoritative_write_authorized=False,
        )

    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.lifecycle.synthesize_with_document_handoff",
        mock_synthesize,
    )
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.strategies.qa.FullMeetingQAGateStrategy.evaluate",
        mock_evaluate,
    )
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.lifecycle.check_directory_compliance",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.attempt_binding.BoundAttemptLedger.record",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.attempt_binding.BoundAttemptLedger.restore",
        lambda self, att, s, e, sm: (s, e),
    )

    meta = {"project": "test_proj"}
    attempt, s_res, e_res = run_synthesis_loop(
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        provider=None,
        provider_type="offline",
        model_name="mock-model",
        reasoning_effort="high",
        source_text="source content for test",
        pa_context={},
        metadata=meta,
        out_p=tmp_path,
        start_t=0.0,
        max_judge_retries=2,
    )

    # Attempt 1 synthesized and evaluated (failed).
    # Attempt 2 synthesized, but detected unchanged candidate; loop terminated BEFORE evaluate 2.
    assert call_counts["synthesize"] == 2
    assert call_counts["evaluate"] == 1
    assert e_res.authoritative_write_authorized is False


def test_transcript_resume_reuses_validated_synthesis_after_downstream_interruption(monkeypatch, tmp_path):
    from dual_pipeline.canonical_pipeline.transcript_resume import (
        record_transcript_failure,
        run_synthesis_with_transcript_resume,
    )
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult

    ckpt_file = tmp_path / "checkpoints" / "transcript_test.json"
    meta = {
        "project": "test_proj",
        "run_id": "run-1",
        "_transcript_checkpoint_path": str(ckpt_file),
    }

    synth_mock = SynthesisResult(
        output_1="valid_o1",
        output_2="valid_o2",
        output_3='{"graph_mutations": {}}',
        attempt=1,
    )
    eval_mock = QAEvaluationResult(
        passed=True,
        qa_report={"overall_verdict": "PASS"},
        judge_report={"status": "PASS", "overall_verdict": "PASS"},
        final_decision={"status": "PASS"},
        authoritative_write_authorized=True,
    )

    synth_calls = 0

    def mock_synthesize(**kwargs):
        nonlocal synth_calls
        synth_calls += 1
        return 1, synth_mock, eval_mock

    # Bypass deep attempt binding and gate evaluators for unit test
    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.transcript_resume.verify_attempt_binding",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates",
        lambda *a, **k: {"overall_verdict": "PASS", "all_gates_passed": True},
    )

    from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
    provider = OfflineTranscriptSynthesizer()

    # Run 1: Stage L2 completes and validates synthesis
    res1 = run_synthesis_with_transcript_resume(
        synthesize=mock_synthesize,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="source text",
        metadata=meta,
        provider=provider,
        provider_type="offline",
    )
    assert synth_calls == 1
    assert meta["transcript_resume"]["reused_synthesis"] is False

    # Downstream failure injected
    record_transcript_failure(meta, "X_EXIT_GATES", RuntimeError("Simulated downstream crash"))

    # Run 2: Resume with same source and bindings
    meta2 = {
        "project": "test_proj",
        "run_id": "run-2",
        "_transcript_checkpoint_path": str(ckpt_file),
    }
    res2 = run_synthesis_with_transcript_resume(
        synthesize=mock_synthesize,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="source text",
        metadata=meta2,
        provider=provider,
        provider_type="offline",
    )

    # Synthesis function was NOT called again; reused cached synthesis!
    assert synth_calls == 1
    assert meta2["transcript_resume"]["reused_synthesis"] is True
    assert res2[0] == 1
    assert res2[1].output_1 == "valid_o1"


def test_review_required_never_reusable_success(monkeypatch, tmp_path):
    from dual_pipeline.canonical_pipeline.transcript_resume import run_synthesis_with_transcript_resume
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
    from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer

    provider = OfflineTranscriptSynthesizer()
    ckpt_file = tmp_path / "checkpoints" / "transcript_rr.json"
    meta = {
        "project": "test_proj",
        "run_id": "run-rr",
        "_transcript_checkpoint_path": str(ckpt_file),
    }

    synth_mock = SynthesisResult(
        output_1="review_o1",
        output_2="review_o2",
        output_3='{"graph_mutations": {}}',
        attempt=1,
    )
    eval_mock = QAEvaluationResult(
        passed=False,
        qa_report={},
        judge_report={"status": "REVIEW_REQUIRED"},
        final_decision={"status": "REVIEW_REQUIRED"},
        authoritative_write_authorized=False,
    )

    synth_calls = 0

    def mock_synthesize(**kwargs):
        nonlocal synth_calls
        synth_calls += 1
        return 1, synth_mock, eval_mock

    monkeypatch.setattr(
        "dual_pipeline.canonical_pipeline.transcript_resume.verify_attempt_binding",
        lambda *a, **k: None,
    )

    res1 = run_synthesis_with_transcript_resume(
        synthesize=mock_synthesize,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="source text",
        metadata=meta,
        provider=provider,
        provider_type="offline",
    )
    assert synth_calls == 1
    assert res1[2].final_decision["status"] == "REVIEW_REQUIRED"

    # Second run must NOT reuse REVIEW_REQUIRED as success
    meta2 = {
        "project": "test_proj",
        "run_id": "run-rr-2",
        "_transcript_checkpoint_path": str(ckpt_file),
    }
    run_synthesis_with_transcript_resume(
        synthesize=mock_synthesize,
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        source_text="source text",
        metadata=meta2,
        provider=provider,
        provider_type="offline",
    )
    assert synth_calls == 2
    assert meta2["transcript_resume"]["reused_synthesis"] is False


def test_pre_synthesis_knowable_constraints_validation(tmp_path):
    import pytest
    from dual_pipeline.canonical_pipeline.synthesis_loop_support import validate_pre_synthesis_constraints
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

    # Empty source text fails
    with pytest.raises(ValueError, match="empty or invalid"):
        validate_pre_synthesis_constraints(CanonicalModality.MEETING_TRANSCRIPT, "", {}, tmp_path)

    # Contradictory options fail
    with pytest.raises(ValueError, match="contradictory options"):
        validate_pre_synthesis_constraints(
            CanonicalModality.MEETING_NOTES,
            "valid source",
            {"output3_only": True},
            tmp_path,
        )

