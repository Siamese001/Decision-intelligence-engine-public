"""
Adversarial & edge-case tests for Wave 3 (Combinations 1 & 2):
- Boundary payload: truncated 1-turn Output 1 checkpoint vs 10-turn source transcript rejected by _is_output_1_truncated.
- Malformed input: corrupted stage_seals.json handled gracefully and re-bootstrapped when valid 01_*/02_* exist.
- Invariant bypass attempt: tampered frozen artifact SHA-256 rejected by _check_frozen_sha_match.
- Rollback & recovery: terminal attempt rollback restores high-water mark candidate and seals it on disk; cross-worktree inheritance prioritizes explicit input_path worktree and seeds prior artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _is_output_1_truncated
from dual_pipeline.canonical_pipeline.evaluator import (
    _merge_pinned_into_judge_report,
    _sync_and_reseal_evaluation,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.worktree_inheritance import (
    _seed_prior_artifacts_into_worktree,
    inherit_worktree_from_metadata,
)
from dual_pipeline.remediation_core.artifact_validator import (
    _check_frozen_sha_match,
    validate_frozen_artifact_gate,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


def test_boundary_truncated_output1_checkpoint_rejected() -> None:
    """Boundary payload: 1-turn checkpoint against a 12-turn source transcript is flagged as truncated."""
    source_12_turns = "\n".join(
        f"**[00:00:{i:02d}] Speaker {1 if i % 2 == 0 else 2}:** Turn number {i} content."
        for i in range(1, 13)
    )
    truncated_ckpt = "**[00:00:01] Speaker 1:** Only one turn."
    assert _is_output_1_truncated(truncated_ckpt, {"source_text": source_12_turns}) is True

    full_ckpt = source_12_turns
    assert _is_output_1_truncated(full_ckpt, {"source_text": source_12_turns}) is False


def test_malformed_stage_seals_and_sha_mismatch_detection(tmp_path: Path) -> None:
    """Malformed input & Invariant bypass: corrupt stage_seals.json recovers from valid disk artifact, while explicit SHA mismatch fails closed."""
    out_dir = tmp_path / "corrupt_seals_dir"
    out_dir.mkdir(parents=True, exist_ok=True)

    valid_f1 = (
        "# Forensic Transcript\n**Date:** 10/16/2026\n\n"
        "**[00:00:01] Alice Smith:** First turn with enough content to exceed 100 characters easily.\n"
        "**[00:00:05] Bob Jones:** Second turn confirming architecture milestones for Q4.\n"
    )
    (out_dir / "01_test_hitl_resolved_transcript.md").write_text(valid_f1, encoding="utf-8")
    (out_dir / "stage_seals.json").write_text("{corrupt json", encoding="utf-8")

    res = validate_frozen_artifact_gate(out_dir, "output_1", {"attendees": ["Alice Smith", "Bob Jones"]})
    assert res.valid is True

    # Invariant bypass: tampered SHA against certified final_release_decision manifest
    fake_sha = "0" * 64
    err = _check_frozen_sha_match(
        "output_1",
        valid_f1,
        {"artifact_manifest": {"output_1_sha256": fake_sha}},
        {},
    )
    assert err is not None
    assert "SHA256 mismatch" in err


def test_healed_frozen_output1_and_output2_preserved_in_judge_sync() -> None:
    """Verifies frozen Output 1 and Output 2 retain PASS status even when healed SHA differs from prior pinned SHA."""
    sm = SelectiveRemediationStateMachine(max_attempts=2)
    healed_f1 = "# Healed Output 1\n**[00:00:01] Alice Smith:** Hello."
    healed_f2 = "# Healed Output 2\nSummary."
    f3 = json.dumps({"schema_version": "5.7", "evidence_index": {}, "graph_mutations": {}})
    sm.freeze_artifact("output_1", healed_f1)
    sm.permanently_frozen.add("output_1")
    sm.freeze_artifact("output_2", healed_f2)
    sm.permanently_frozen.add("output_2")

    syn = SynthesisResult(output_1=healed_f1, output_2=healed_f2, output_3=f3)
    old_sha = hashlib.sha256(b"unhealed_prior_content").hexdigest()
    pinned = {
        "output_1": {
            "composite_score": 96.0,
            "verdict": "PASS",
            "artifact_sha256": old_sha,
            "dimension_scores": {"completeness": 96, "accuracy": 96},
        },
        "output_2": {
            "composite_score": 92.0,
            "verdict": "PASS",
            "artifact_sha256": old_sha,
            "dimension_scores": {"completeness": 92, "accuracy": 92},
        },
    }
    judge_rep = {
        "output_1_evaluation": {"status": "SKIPPED", "reason": "FROZEN_PRIOR_ARTIFACT"},
        "output_2_evaluation": {"status": "SKIPPED", "reason": "FROZEN_PRIOR_ARTIFACT"},
        "output_3_evaluation": {"composite_score": 93.0, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True, "status": "PASS"},
    }
    qa_rep = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    final_dec = {}
    meta = {"state_machine": sm}

    _merge_pinned_into_judge_report(judge_rep, pinned, meta=meta, synthesis_result=syn)
    auth = _sync_and_reseal_evaluation(
        judge_rep=judge_rep,
        final_dec=final_dec,
        qa_rep=qa_rep,
        synthesis_result=syn,
        source_text=healed_f1,
        meta=meta,
    )
    assert auth is True
    assert final_dec["output_1_pass"] is True
    assert final_dec["output_2_pass"] is True
    assert final_dec["output_3_pass"] is True


def test_rollback_and_worktree_inheritance_precedence(tmp_path: Path) -> None:
    """Rollback & recovery: terminal rollback restores best candidate; worktree inheritance prioritizes input_path worktree and seeds prior files."""
    ckpt_dir = tmp_path / "ckpt"
    sm = SelectiveRemediationStateMachine(max_attempts=2, checkpoint_dir=ckpt_dir)
    sm.current_attempt = 1
    sm.record_candidate("output_3", '{"good": 1}', composite_score=88, verdict="FAIL")
    sm.current_attempt = 2
    sm.record_candidate("output_3", '{"bad_regression": 0}', composite_score=72, verdict="FAIL")

    artifacts = {"output_3": '{"bad_regression": 0}'}
    sm._check_monotonic_regressions(artifacts)
    assert artifacts["output_3"] == '{"good": 1}'

    # Worktree inheritance from explicit input_path inside .worktrees/<slug>
    fake_repo = tmp_path / "repo"
    wt_target = fake_repo / ".worktrees" / "audio_platform_sync"
    wt_out = wt_target / "output" / "internal_consulting" / "10-16-2026" / "Platform_Sync"
    wt_out.mkdir(parents=True, exist_ok=True)
    inp_file = wt_out / "01_Platform_Sync_hitl_resolved_transcript.md"
    inp_file.write_text("# Transcript", encoding="utf-8")

    inherited = inherit_worktree_from_metadata({"input_path": str(inp_file)}, repo_root=fake_repo)
    assert inherited is not None
    assert inherited[0].resolve() == wt_target.resolve()

    # Seeding prior artifacts into newly provisioned worktree target
    main_out = fake_repo / "output" / "internal_consulting" / "10-16-2026" / "Platform_Sync"
    main_out.mkdir(parents=True, exist_ok=True)
    (main_out / "02_Platform_Sync_executive_recap.md").write_text("# Recap", encoding="utf-8")
    (main_out / "stage_seals.json").write_text('{"output_1_seal": true}', encoding="utf-8")

    new_wt_out = fake_repo / ".worktrees" / "ingest_meeting_new" / "output" / "internal_consulting" / "10-16-2026" / "Platform_Sync"
    _seed_prior_artifacts_into_worktree(main_out, new_wt_out)
    assert (new_wt_out / "02_Platform_Sync_executive_recap.md").is_file()
    assert (new_wt_out / "stage_seals.json").is_file()
