"""Wave 2 E2E tests: Document resume binding, conversion cache purity, and worktree code freeze."""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.document_resume import (
    lock_document_source,
    record_document_failure,
    refresh_validated_document_checkpoint,
    reset_document_checkpoint,
    run_synthesis_with_resume,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.cli.ingest_worktree_guard import (
    IngestWorktreeCodeFreezeError,
    assert_ingest_worktree_code_freeze,
)
from dual_pipeline.document_pipeline_core.content_loader import (
    extract_document_text_snippet,
    load_document_content,
)
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_release_evaluation_digest,
)


class _DummyProvider:
    model = "gpt-test-ext"
    d03_model = "gpt-test-ext"
    model_name = "gpt-test-ext"
    model_revision = "rev1"


def _make_valid_bundle(source_text: str, output_3_str: str) -> tuple[SynthesisResult, QAEvaluationResult]:
    syn = SynthesisResult(output_1="", output_2="", output_3=output_3_str)
    qa_report = {
        "passed": True,
        "healed_artifacts": {"output_1": "", "output_2": "", "output_3": output_3_str},
        "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
    }
    judge_report: dict[str, Any] = {
        "rubric_version": "2.0",
        "mode": "document",
        "evaluator_model": "gpt-test-judge",
        "reasoning_effort": "high",
        "status": "PASS",
        "execution": {
            "status": "CALLED",
            "effective_model": "gpt-test-judge",
            "effective_effort": "high",
            "model_identity": "gpt-test-judge:rev1",
            "provider_type": "offline",
        },
        "extraction_execution": {
            "status": "CALLED",
            "effective_model": "gpt-test-ext",
            "effective_effort": "high",
            "model_identity": "gpt-test-ext:rev1",
            "provider_type": "offline",
        },
        "output_3_evaluation": {
            "status": "PASS",
            "verdict": "PASS",
            "composite_score": 95,
            "dimension_scores": {
                "completeness": 95,
                "grounding": 95,
                "entity_resolution": 95,
                "schema_compliance": 95,
            },
        },
    }
    eval_digest = compute_release_evaluation_digest(judge_report)
    judge_report["evaluation_receipt_digest"] = eval_digest
    manifest = build_artifact_manifest(
        f1_text=source_text,
        f2_text="",
        f3_text=output_3_str,
        model="gpt-test-judge",
        reasoning_effort="high",
        judge_eval_digest=eval_digest,
    )
    final_decision = {
        "mode": "document",
        "attempt": 1,
        "max_attempts": 2,
        "output_1_deterministic_gates_pass": True,
        "output_1_llm_score_pass": True,
        "output_1_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_2_llm_score_pass": True,
        "output_2_pass": True,
        "output_3_deterministic_gates_pass": True,
        "output_3_llm_score_pass": True,
        "output_3_pass": True,
        "cross_artifact_consistency_pass": True,
        "final_pass": True,
        "authoritative_write_authorized": True,
        "status": "PASS",
        "judge_evaluation_digest": eval_digest,
        "policy_version": "2.0.0",
        "rubric_version": "2.0",
        "artifact_manifest": manifest,
    }
    ev = QAEvaluationResult(
        passed=True,
        qa_report=qa_report,
        judge_report=judge_report,
        final_decision=final_decision,
        authoritative_write_authorized=True,
    )
    return syn, ev


def test_snippet_before_full_load_preserves_structural_units(tmp_path: Path) -> None:
    """CAT1-1000-P0-2: extract_document_text_snippet(metadata=None) never poisons persistent cache with null units."""
    doc_path = tmp_path / "architecture_deck.md"
    doc_path.write_text("# Slide 1: Overview\nKey architecture points.\n\n# Slide 2: Risks\nMitigation plan.\n", encoding="utf-8")
    cache_dir = tmp_path / "conv_cache"

    # 1. Call metadata-less snippet extraction first
    snippet = extract_document_text_snippet(doc_path, max_chars=50, metadata=None)
    assert "Slide 1" in snippet

    # 2. Now run full document content load with metadata and cache_dir
    meta: dict[str, Any] = {"project": "test_proj", "conversion_cache_dir": str(cache_dir)}
    text, sha, fmt = load_document_content(doc_path, metadata=meta, cache_dir=cache_dir)
    assert "Slide 2" in text
    assert meta.get("source_units") is not None
    assert len(meta["source_units"]) >= 1

    # Verify cache JSON on disk has non-null source_units
    cache_jsons = list(cache_dir.glob("*.json"))
    assert len(cache_jsons) == 1
    cached_data = json.loads(cache_jsons[0].read_text(encoding="utf-8"))
    assert cached_data.get("source_units") is not None
    assert len(cached_data["source_units"]) >= 1


def test_exit_gate_invalidation_blocks_disk_attempt_resurrection_on_resume(tmp_path: Path) -> None:
    """CAT1-1000-P0-1: X_EXIT_GATES failure invalidates synthesis and blocks out_p disk fallback."""
    reset_document_checkpoint()
    source_text = "# Strategic Plan\nDetailed content for ingestion."
    out_p = tmp_path / "out_doc"
    out_p.mkdir(parents=True, exist_ok=True)
    doc_path = tmp_path / "plan.md"
    doc_path.write_text(source_text, encoding="utf-8")

    meta: dict[str, Any] = {
        "is_document": True,
        "project": "internal_consulting",
        "_document_source_path": str(doc_path),
        "extraction_model_revision": "rev1",
        "judge_model_revision": "rev1",
        "run_id": "run_1",
    }
    with contextlib.ExitStack() as stack:
        lock_document_source(stack, meta, source_text, tmp_path / "runtime")

    f3_str = json.dumps({"entities": [], "assertions": [], "decisions": []})
    syn1, ev1 = _make_valid_bundle(source_text, f3_str)
    call_count = 0

    def _fake_synthesize(**_kw: Any) -> tuple[int, SynthesisResult, QAEvaluationResult]:
        nonlocal call_count
        call_count += 1
        return 1, syn1, ev1

    base_kwargs = {
        "modality": CanonicalModality.DOCUMENT,
        "provider": _DummyProvider(),
        "provider_type": "offline",
        "model_name": "gpt-test-ext",
        "reasoning_effort": "high",
        "source_text": source_text,
        "pa_context": {},
        "metadata": meta,
        "out_p": out_p,
        "max_judge_retries": 1,
        "judge_model": "gpt-test-judge",
        "judge_effort": "high",
    }

    with (
        patch("dual_pipeline.canonical_pipeline.execution_identity.execution_reuse_blockers", return_value=[]),
        patch("dual_pipeline.document_pipeline_core.evaluator.evaluate_document_qa_gates", return_value={"passed": True, "healed_artifacts": {"output_3": f3_str}}),
        patch("dual_pipeline.document_pipeline_core.quality.qualify_document_judge", return_value=({}, True, 95)),
    ):
        # Run 1: populates VALIDATED_SYNTHESIS checkpoint
        run_synthesis_with_resume(synthesize=_fake_synthesize, **base_kwargs)
        assert call_count == 1

        # Simulate Stage X_EXIT_GATES failure invalidating the synthesis
        record_document_failure(meta, "X_EXIT_GATES", RuntimeError("Exit gate X2 failed"))

        # Run 2: Even though try_resume_passing_attempt would return (1, syn1, ev1) from out_p,
        # disk fallback MUST be blocked because X_EXIT_GATES invalidated the synthesis!
        with patch("dual_pipeline.canonical_pipeline.attachments.try_resume_passing_attempt", return_value=(1, syn1, ev1)):
            run_synthesis_with_resume(synthesize=_fake_synthesize, **base_kwargs)
        assert call_count == 2
        assert meta["document_resume"]["reused_synthesis"] is False


def test_refresh_validated_document_checkpoint_updates_digests_and_clears_pending(tmp_path: Path) -> None:
    """CAT1-1000-P0-1: refresh_validated_document_checkpoint updates attempt_binding digests after exit-gate auto-heal."""
    reset_document_checkpoint()
    source_text = "# Financial Summary\nRevenue targets for Q4."
    doc_path = tmp_path / "fin.md"
    doc_path.write_text(source_text, encoding="utf-8")
    meta: dict[str, Any] = {
        "is_document": True,
        "project": "internal_consulting",
        "_document_source_path": str(doc_path),
        "extraction_model_revision": "rev1",
        "judge_model_revision": "rev1",
        "defer_checkpoint_until_exit_qa": True,
        "source_text": source_text,
        "run_id": "run_heal",
    }
    with contextlib.ExitStack() as stack:
        lock_document_source(stack, meta, source_text, tmp_path / "runtime")

    initial_f3 = json.dumps({"entities": [], "assertions": []})
    healed_f3 = json.dumps({"entities": [{"id": "ent-1"}], "assertions": []})
    syn_initial, ev_initial = _make_valid_bundle(source_text, initial_f3)

    base_kwargs = {
        "modality": CanonicalModality.DOCUMENT,
        "provider": _DummyProvider(),
        "provider_type": "offline",
        "model_name": "gpt-test-ext",
        "reasoning_effort": "high",
        "source_text": source_text,
        "pa_context": {},
        "metadata": meta,
        "out_p": tmp_path / "out_heal",
        "max_judge_retries": 1,
        "judge_model": "gpt-test-judge",
        "judge_effort": "high",
    }

    with (
        patch("dual_pipeline.canonical_pipeline.execution_identity.execution_reuse_blockers", return_value=[]),
        patch("dual_pipeline.document_pipeline_core.evaluator.evaluate_document_qa_gates", return_value={"passed": True, "healed_artifacts": {"output_3": healed_f3}}),
        patch("dual_pipeline.document_pipeline_core.quality.qualify_document_judge", return_value=({}, True, 95)),
    ):
        run_synthesis_with_resume(synthesize=lambda **_kw: (1, syn_initial, ev_initial), **base_kwargs)
        # Simulate exit-gate auto-heal modifying output_3
        syn_healed = SynthesisResult(output_1="", output_2="", output_3=healed_f3)
        refresh_validated_document_checkpoint(syn_healed, ev_initial, 1, meta)

        from dual_pipeline.checkpoint_storage import read_checkpoint
        raw_ckpt = read_checkpoint(Path(meta["_document_checkpoint_path"]))
        assert raw_ckpt is not None
        assert "PENDING_EXIT_QA_SYNTHESIS" not in raw_ckpt["stages"]
        assert "VALIDATED_SYNTHESIS" in raw_ckpt["stages"]

        # Subsequent resume must validate healed f3_sha256 without raising CACHED_SYNTHESIS_INVALID
        meta["resume"] = True
        resumed_att, resumed_syn, _ = run_synthesis_with_resume(
            synthesize=lambda **_kw: pytest.fail("Should have resumed healed synthesis"),
            **base_kwargs,
        )
        assert resumed_att == 1
        assert resumed_syn.output_3 == healed_f3


def test_ingest_worktree_code_freeze_blocks_dirty_tools_on_audio_and_transcript_branches(tmp_path: Path) -> None:
    """CAT1-1000-P0-3: Dirty tools/ file on audio/* or transcript/* raises IngestWorktreeCodeFreezeError."""
    for branch_name in ("audio/meeting_2026", "transcript/meeting_2026", "ingest/doc_2026"):
        with (
            patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.get_current_git_branch", return_value=branch_name),
            patch("dual_pipeline.live_meeting_recorder_core.worktree_manager.run_git", side_effect=[" M tools/readiness_core.py\n", ""]),
        ):
            with pytest.raises(IngestWorktreeCodeFreezeError, match="code-frozen"):
                assert_ingest_worktree_code_freeze(tmp_path)
