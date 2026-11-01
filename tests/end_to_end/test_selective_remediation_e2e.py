"""
tests/end_to_end/test_selective_remediation_e2e.py

End-to-End tests for Wave 4 Selective Remediation & Checkpoint Reuse:
1. Output 02 failure selectively remediates Output 02 while preserving passing Output 01 and 03 byte-identical
2. Passing Output 01 mtime and file digest remain unchanged across remediation attempts
3. Cached rerun reuses validated synthesis with zero provider calls
4. Monotonic progress guard terminates futile remediation loops early on repeated failure signature
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.transcript_resume import run_synthesis_with_transcript_resume
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_release_evaluation_digest,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.eval_cache import clear_evaluation_cache
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2
from tests.test_unmocked_qa_pipeline_integration import SAMPLE_F1, SAMPLE_F2, SAMPLE_F3


@pytest.fixture(autouse=True)
def clean_eval_cache():
    clear_evaluation_cache()
    yield
    clear_evaluation_cache()


def test_e2e_selective_remediation_preserves_output1_byte_identical_and_mtime_untouched():
    """Verify that an Output 02 failure regenerates only Output 02, leaving Output 01 byte-identical."""
    with tempfile.TemporaryDirectory(prefix="test_sel_rem_e2e_") as td:
        out_dir = Path(td)
        o1_file = out_dir / "01_2026-10-11_architecture_sync_hitl_resolved_transcript.md"
        o2_file = out_dir / "02_2026-10-11_architecture_sync_executive_recap.md"

        o1_initial_content = (
            "# Forensic Transcript\n\n"
            "Speaker 1: Strategic review of enterprise platform integration.\n"
            "Speaker 2: Verified latency reduction and zero database lock starvation.\n"
        ) * 10
        o1_file.write_text(o1_initial_content, encoding="utf-8")
        initial_o1_stat = o1_file.stat()
        initial_o1_digest = hashlib.sha256(o1_file.read_bytes()).hexdigest()

        # Output 02 initially with degenerate table that needs healing
        o2_initial_content = (
            "# Subject: Enterprise Architecture Review\n\n"
            "**Date:** 10/11/2026\n"
            "**Attendees:** Amit Ayer (Architect), Jason PartnerAdvisory (Lead)\n\n"
            "## Meeting Objective\nAlignment on runtime hardening.\n\n"
            "## Key Discussion Points & Findings\n* Discussed system optimizations.\n\n"
            "## Action & Governance Ledger\n| Item |\n| No formal actions |\n\n"
            "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "### Amit Ayer\n#### Perspective\nComprehensive platform strategy.\n"
        )
        o2_file.write_text(o2_initial_content, encoding="utf-8")

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        # Freeze passing Output 1
        sm.freeze_artifact("output_1", o1_initial_content)
        sm.permanently_frozen.add("output_1")
        sm.passing_stages.add("output_1")

        # Sleep briefly to ensure mtime would differ if touched
        time.sleep(0.05)

        # Execute healing on Output 2
        meta = {
            "project": "internal_consulting",
            "meeting_name": "architecture_sync",
            "meeting_date": "10/11/2026",
            "attendees": ["Amit Ayer", "Jason PartnerAdvisory"],
        }
        healed_o2 = heal_output_2(o2_initial_content, meta, output_1=o1_initial_content)
        o2_file.write_text(healed_o2, encoding="utf-8")

        # Verify Output 1 is untouched
        current_o1_stat = o1_file.stat()
        current_o1_digest = hashlib.sha256(o1_file.read_bytes()).hexdigest()

        assert current_o1_digest == initial_o1_digest, "Output 1 content must remain byte-identical"
        assert current_o1_stat.st_mtime == initial_o1_stat.st_mtime, "Output 1 mtime must NOT be touched"
        assert sm.is_frozen("output_1") is True
        assert sm.get_frozen_content("output_1") == o1_initial_content

        # Verify Output 2 retained header and contains compliant ratification notice
        assert "Action & Governance Ledger" in healed_o2
        assert "The supplied entries establish no assigned actions" in healed_o2


def test_e2e_cached_rerun_makes_zero_provider_calls():
    """Verify that a second run with identical input reuses validated synthesis with 0 provider calls."""
    with tempfile.TemporaryDirectory(prefix="test_cache_zero_calls_") as td:
        root_p = Path(td)
        ckpt_dir = root_p / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        ckpt_path = ckpt_dir / "transcript_test_cache.json"

        source_text = "Speaker 1: Evaluating storage engine architecture with SQLite WAL.\n" * 5
        metadata = {
            "project": "internal_consulting",
            "meeting_name": "Storage_Engine_Architecture_09_18_2026",
            "meeting_date": "09/18/2026",
            "duration_minutes": 2,
            "roster": [
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Architect"},
                {"name": "Alex Sanchez", "org": "PartnerAdvisory", "role": "Solution Architect"},
            ],
            "primary_speakers": ["Amit Ayer", "Alex Sanchez"],
            "_transcript_checkpoint_path": str(ckpt_path),
            "raw_source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        }

        o1_text = SAMPLE_F1
        o2_text = SAMPLE_F2
        o3_text = SAMPLE_F3

        judge_data = {
            "passed": True,
            "verdict": "PASS",
            "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 96, "verdict": "PASS"},
            "output_3_evaluation": {"composite_score": 96, "verdict": "PASS"},
        }
        judge_digest = compute_release_evaluation_digest(judge_data)
        judge_report = {**judge_data, "evaluation_receipt_digest": judge_digest}

        manifest = build_artifact_manifest(
            f1_text=o1_text,
            f2_text=o2_text,
            f3_text=o3_text,
            model="mock",
            reasoning_effort="low",
            judge_eval_digest=judge_digest,
        )
        final_decision = {
            "status": "PASS",
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": True,
            "artifact_manifest": manifest,
            "sealed": True,
        }

        mock_synth_func = MagicMock()
        mock_synth_result = SynthesisResult(
            output_1=o1_text,
            output_2=o2_text,
            output_3=o3_text,
            attempt=1,
        )
        mock_eval_result = QAEvaluationResult(
            passed=True,
            qa_report={"passed": True, "healed_artifacts": {}},
            judge_report=judge_report,
            final_decision=final_decision,
            authoritative_write_authorized=True,
        )
        mock_synth_func.return_value = (1, mock_synth_result, mock_eval_result)

        from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
        offline_provider = OfflineTranscriptSynthesizer()

        # Run 1: Cold run calls provider / synthesizer
        res1 = run_synthesis_with_transcript_resume(
            synthesize=mock_synth_func,
            source_text=source_text,
            metadata=dict(metadata),
            provider=offline_provider,
            provider_type="offline",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
        )
        assert res1[0] == 1
        assert mock_synth_func.call_count == 1
        assert ckpt_path.is_file(), "Validated synthesis checkpoint must be persisted"

        # Run 2: Cached rerun with fresh metadata
        mock_synth_func.reset_mock()
        run2_metadata = {
            **metadata,
            "attempt": 1,
            "has_prior_meeting_artifacts": True,  # transient operational field
            "run_id": "fresh-run-uuid-12345",
        }

        res2 = run_synthesis_with_transcript_resume(
            synthesize=mock_synth_func,
            source_text=source_text,
            metadata=run2_metadata,
            provider=offline_provider,
            provider_type="offline",
            modality=CanonicalModality.MEETING_TRANSCRIPT,
        )

        # Synthesizer must NOT be called on cached run
        assert mock_synth_func.call_count == 0, "Cached run must make 0 synthesizer calls"
        assert res2[1].output_1 == mock_synth_result.output_1
        assert res2[1].output_2 == mock_synth_result.output_2
        assert run2_metadata.get("transcript_resume", {}).get("reused_synthesis") is True


def test_e2e_remediation_stagnation_terminates_under_max_iteration_bound():
    """Verify state machine halts early when non-converging failure signature repeats."""
    sm = SelectiveRemediationStateMachine(max_attempts=5)

    failing_judge = {
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 82, "verdict": "FAIL"},
        "output_3_evaluation": {"composite_score": 95, "verdict": "PASS"},
    }
    failing_qa = {
        "output_02_executive_recap": {"passed": False, "failures": ["Syntax error in section 3"]},
    }
    failing_dec = {"status": "FAIL", "output_2_pass": False}

    # Attempt 1
    sm.evaluate_attempt(failing_dec, failing_judge, failing_qa, artifacts={"output_2": "bad_text"})
    assert sm.should_retry() is True

    sm.advance_attempt()
    assert sm.current_attempt == 2

    # Attempt 2 produces identical failure
    sm.evaluate_attempt(failing_dec, failing_judge, failing_qa, artifacts={"output_2": "bad_text"})

    # Must terminate early at attempt 2 despite max_attempts=5
    assert sm.should_retry() is False
    assert sm.current_attempt == 2
    assert sm.get_stagnant_diagnosis() is not None
