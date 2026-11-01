"""
Adversarial & Edge-Case tests for Wave 5 (CAT-4 P0 & P1):
Covers >= 3 mandatory categories:
1. Invariant bypass attempt: _can_reuse_healed_output_3 rejects Gate 3 failure even when check_conflicting_duplicates passes; _validate_cached_transcript rejects cached synthesis when evaluate_all_qa_gates returns overall_pass=False.
2. Malformed input: promote_staging_to_release fails closed on corrupt JSON in metadata.json or final_release_decision.json and preserves staging_dir on failure.
3. Rollback & recovery / Production regression: _sweep_stale_assertions refuses to mutate default live graph or sealed generation DB when graph_path is omitted or sealed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
    SynthesisResult,
)
from dual_pipeline.canonical_pipeline.transcript_resume import (
    _validate_cached_transcript,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    _sweep_stale_assertions,
    promote_staging_to_release,
)


def test_can_reuse_healed_output_3_rejects_gate3_failure(tmp_path: Path) -> None:
    att_dir = tmp_path / "attempt_1"
    att_dir.mkdir(parents=True)
    cand_file = att_dir / "output_3.ckpt.json"
    cand_file.write_text('{"entities": []}', encoding="utf-8")
    (att_dir / "final_release_decision.json").write_text(
        json.dumps({"output_3_deterministic_gates_pass": False, "output_3_pass": False}),
        encoding="utf-8",
    )

    with patch(
        "dual_pipeline.transcript_qa_core.gate3.run_qa_gate_3",
        return_value={"passed": False, "failures": ["G3.1_SCHEMA_ERROR"]},
    ), patch(
        "dual_pipeline.transcript_qa_core.graph_reference_validator.check_conflicting_duplicates",
        return_value=(True, []),
    ):
        reused = FullMeetingSynthesisStrategy._can_reuse_healed_output_3(
            cand_file=cand_file,
            healed_out_3='{"entities": []}',
            forensic_transcript="**[00:01:00] Alice:** Hi.",
            metadata={"resume": True},
        )
        assert reused is False


def test_validate_cached_transcript_rejects_deterministic_qa_regression() -> None:
    syn = SynthesisResult(
        output_1="# Forensic\n**[00:01:00] Alice:** Hi",
        output_2="# Recap\nRecap",
        output_3='{"entities": []}',
    )
    ev = QAEvaluationResult(
        passed=True,
        qa_report={"overall_pass": True},
        judge_report={"verdict": "PASS"},
        final_decision={"status": "PASS", "authoritative_write_authorized": True},
        authoritative_write_authorized=True,
    )
    payload = {
        "attempt": 1,
        "synthesis": {
            "output_1": syn.output_1,
            "output_2": syn.output_2,
            "output_3": syn.output_3,
        },
        "evaluation": {
            "passed": True,
            "qa_report": ev.qa_report,
            "judge_report": ev.judge_report,
            "final_decision": ev.final_decision,
            "authoritative_write_authorized": True,
        },
        "exit_gates_verified": True,
    }

    with patch("dual_pipeline.canonical_pipeline.transcript_resume.verify_attempt_binding"), patch(
        "dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates",
        return_value={"overall_pass": False, "gate_2": {"passed": False, "failures": ["G2_FAIL"]}},
    ):
        with pytest.raises(ValueError, match="Current deterministic QA no longer accepts"):
            _validate_cached_transcript(
                payload=payload,
                source_text="raw",
                metadata={},
                modality="MEETING_TRANSCRIPT",
            )


def test_promote_staging_to_release_fails_closed_on_corrupt_json_and_keeps_staging(tmp_path: Path) -> None:
    staging = tmp_path / ".staging_run"
    target = tmp_path / "release_run"
    staging.mkdir(parents=True)
    (staging / "metadata.json").write_text("{corrupt json", encoding="utf-8")
    (staging / "01_Transcript.md").write_text("# Transcript", encoding="utf-8")

    with pytest.raises(RuntimeError, match="corrupt staging metadata.json"):
        promote_staging_to_release(staging, target, purge_staging=True)

    # Staging directory must be preserved when promotion fails
    assert staging.exists()
    assert not target.exists()


def test_sweep_stale_assertions_skips_when_graph_path_omitted_or_sealed(tmp_path: Path) -> None:
    written: dict[str, str] = {}
    with patch("storage.connection.create_sqlite_conn") as mock_conn:
        _sweep_stale_assertions({}, written)
        mock_conn.assert_not_called()

    sealed_dir = tmp_path / "generations" / "gen-001"
    sealed_dir.mkdir(parents=True)
    sealed_db = sealed_dir / "graph.sqlite"
    sealed_db.write_text("", encoding="utf-8")
    with patch("storage.connection.create_sqlite_conn") as mock_conn, patch(
        "dual_pipeline.storage.governed_context.is_sealed_generation_database",
        return_value=True,
    ):
        _sweep_stale_assertions({"graph_path": str(sealed_db)}, written)
        mock_conn.assert_not_called()
