"""
tests/test_transcript_synthesis_e2e.py

End-to-End and integration tests for transcript synthesis:
1. Frozen Output 03 quote-pointer and exact_text re-anchoring when Output 01 dialogue changes.
2. Cross-output coherence verification (X3 gate).
3. Active CWD worktree precedence over stale metadata paths.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.evaluator import (
    _apply_healed,
    _merge_pinned_into_judge_report,
    _pre_heal_output_3,
)
from dual_pipeline.canonical_pipeline.worktree_inheritance import (
    inherit_worktree_from_metadata,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    SynthesisResult,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    MeetingXExitContract,
)
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator


def _build_sample_f3_data(
    exact_text: str = "Through the um well we have two roles open currently in platform engineering.",
    start: int = 50,
    end: int = 127,
    locator_ts: str = "00:04:47",
    speaker: str = "person:megan-orzell",
) -> Dict[str, Any]:
    return {
        "schema_version": "5.7",
        "meeting_id": "meet-e2e-sync",
        "classification": "internal",
        "audiences": [],
        "evidence_index": {
            "ev-02": {
                "statement_id": "ev-02",
                "evidence_id": "ev-02",
                "speaker": speaker,
                "text": exact_text,
                "exact_text": exact_text,
                "locator": {"kind": "timestamp", "value": locator_ts},
                "quote_pointer": [start, end],
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:megan-orzell",
                    "type": "PERSON",
                    "name": "Megan Orzell",
                    "properties": {"role": "Engineering Lead", "organization": "PartnerAdvisory"},
                },
                {
                    "id": "person:amit-ayer",
                    "type": "PERSON",
                    "name": "Amit Ayer",
                    "properties": {"role": "Practice Director", "organization": "PartnerAdvisory"},
                },
            ],
            "relationship_signals": [
                {
                    "from_id": "person:megan-orzell",
                    "relationship": "HAS_ROLE",
                    "to_id": "role:lead",
                }
            ],
        },
        "evidence_only_statement_ids": [],
    }


def test_quote_pointer_reanchoring_after_filler_scrubbing():
    """Verify quote pointers and exact_text re-anchor cleanly when fillers are scrubbed from Output 01."""
    # Output 01 after filler scrubbing
    f1_text = (
        "# Meeting Transcript\n\n"
        "### [00:04:47]\n\n"
        "**Megan Orzell:** Through the well, we have two roles open currently in platform engineering.\n\n"
        "### [00:05:10]\n\n"
        "**Amit Ayer:** Sounds great."
    )
    recap_text = (
        "# 1. Meeting Objective\n\n"
        "Discuss open roles.\n\n"
        "# 2. Action & Governance Ledger\n\n"
        "| # | Workstream | Action Item | Owner | Target Date |\n"
        "| 1 | Platform | Open candidate requisition | Megan Orzell | 10/20/2026 |\n"
    )

    # Initial f3 data containing pre-scrubbed text with filler words
    f3_data = _build_sample_f3_data(
        exact_text="Through the um well we have two roles open currently in platform engineering.",
        start=50,
        end=127,
        locator_ts="00:04:47",
    )

    evaluator = XExitGatesEvaluator()
    evaluator._heal_quote_pointers(f3_data, transcript=f1_text)
    # Execute with auto_heal=True (pipeline exit behavior)
    passed = evaluator.execute(
        recap=recap_text,
        graph_json=json.dumps(f3_data),
        metadata={"meeting_name": "Discuss open roles", "attendees": ["Megan Orzell", "Amit Ayer"]},
        transcript=f1_text,
        auto_heal=True,
    )
    assert passed is True

    # Validate that quote pointer was updated to point to verbatim span in f1_text
    ev = f3_data["evidence_index"]["ev-02"]
    qp = ev["quote_pointer"]
    reanchored_text = ev["exact_text"]

    assert reanchored_text in f1_text
    assert f1_text[qp[0]:qp[1]] == reanchored_text
    assert qp[1] - qp[0] == len(reanchored_text)

    # Strict contract validation must pass with zero defects
    ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text=f1_text,
        f2_text=recap_text,
        f3_data=f3_data,
        metadata={"attendees": ["Megan Orzell", "Amit Ayer"]},
    )
    assert ok is True, f"X3 validation errors: {errs}"


def test_quote_pointer_reanchoring_after_backchannel_turn_splitting():
    """Verify quote pointers re-anchor when dialogue is split by an interleaved backchannel turn."""
    # Output 01 where a backchannel turn was split out
    f1_text = (
        "### [00:04:40]\n\n"
        "**Amit Ayer:** Go ahead.\n\n"
        "### [00:04:47]\n\n"
        "**Megan Orzell:** We have two roles open currently in platform engineering. Both are critical.\n\n"
        "### [00:05:00]\n\n"
        "**Amit Ayer:** Perfect."
    )
    recap_text = (
        "# 1. Meeting Objective\n\n"
        "Discuss open roles.\n\n"
        "# 2. Action & Governance Ledger\n\n"
        "| # | Workstream | Action Item | Owner | Target Date |\n"
        "| 1 | Platform | Open candidate requisition | Megan Orzell | 10/20/2026 |\n"
    )

    # Attempt 1 quote included words before the split
    f3_data = _build_sample_f3_data(
        exact_text="Go ahead. We have two roles open currently in platform engineering.",
        start=10,
        end=82,
        locator_ts="00:04:47",
    )

    evaluator = XExitGatesEvaluator()
    evaluator._heal_quote_pointers(f3_data, transcript=f1_text)

    ev = f3_data["evidence_index"]["ev-02"]
    qp = ev["quote_pointer"]
    reanchored_text = ev["exact_text"]

    assert len(reanchored_text) >= 15
    assert reanchored_text in f1_text
    assert f1_text[qp[0]:qp[1]] == reanchored_text
    assert qp[1] - qp[0] == len(reanchored_text)


def test_frozen_output_3_reanchoring_and_pinned_scorecard_preservation():
    """Verify that when Output 03 is frozen, quote pointers still re-anchor and pinned scorecard binds."""
    f1_before = "### [00:04:47]\n\n**Megan Orzell:** Through the um well we have two roles open."
    f1_after = "### [00:04:47]\n\n**Megan Orzell:** Through the well, we have two roles open."

    f3_data = _build_sample_f3_data(
        exact_text="Through the um well we have two roles open.",
        start=47,
        end=90,
    )
    raw_f3_str = json.dumps(f3_data, indent=2)
    original_f3_sha = hashlib.sha256(raw_f3_str.encode("utf-8")).hexdigest()

    # Mock state machine marking output_3 as frozen
    sm = MagicMock()
    sm.is_frozen.side_effect = lambda k: k == "output_3"

    synth = SynthesisResult(
        output_1=f1_after,
        output_2="# 1. Meeting Objective\nReview.\n# 2. Action & Governance Ledger\n| # | Workstream | Action Item | Owner | Target Date |\n| 1 | W | A | Megan Orzell | 10/20/2026 |\n",
        output_3=raw_f3_str,
    )
    meta = {"state_machine": sm}

    # Pre-heal should re-anchor quote pointers on output_3 even when frozen
    _pre_heal_output_3(synth, meta=meta)
    healed_f3_data = json.loads(synth.output_3)
    healed_sha = hashlib.sha256(synth.output_3.encode("utf-8")).hexdigest()

    ev = healed_f3_data["evidence_index"]["ev-02"]
    assert ev["exact_text"] in f1_after
    assert f1_after[ev["quote_pointer"][0]:ev["quote_pointer"][1]] == ev["exact_text"]

    # Verify _merge_pinned_into_judge_report preserves pinned scorecard
    pinned_card = {
        "rubric_version": "2.0",
        "judge_model": "test-judge",
        "prompt_digest": "a" * 64,
        "sample_count": 1,
        "is_fallback": False,
        "artifact_sha256": original_f3_sha,
        "composite_score": 95.0,
        "verdict": "PASS",
        "dimension_1_strategic_edge": 15.0,
        "dimension_2_knowledge_layer_density": 14.0,
        "dimension_3_semantic_atomicity": 19.0,
        "dimension_4_predicate_specificity": 14.0,
        "dimension_5_exclusion_of_action_items": 14.0,
        "dimension_6_evidence_grounding_and_schema": 19.0,
        "forensic_rationale": "High fidelity canonical projection.",
        "evaluated_span_coverage": 1.0,
    }
    pinned_eval = {"output_3": pinned_card}
    judge_rep = {"output_3_evaluation": {"status": "SKIPPED"}}

    _merge_pinned_into_judge_report(judge_rep, pinned_eval, meta=meta, synthesis_result=synth)

    # Scorecard must be merged with updated artifact_sha256 matching healed output_3
    merged = judge_rep.get("output_3_evaluation")
    assert merged is not None
    assert merged.get("artifact_sha256") == healed_sha
    assert merged.get("verdict") == "PASS"


def test_inherit_worktree_from_metadata_cwd_precedence(tmp_path):
    """Verify active CWD worktree takes precedence over a conflicting meta['worktree_dir']."""
    primary_repo = tmp_path / "repo"
    primary_repo.mkdir()
    (primary_repo / ".git").mkdir()

    wt_active = primary_repo / ".worktrees" / "feat_active_task"
    wt_active.mkdir(parents=True)
    (wt_active / ".git").write_text("gitdir: ...")

    wt_stale = primary_repo / ".worktrees" / "live_stale_metadata"
    wt_stale.mkdir(parents=True)
    (wt_stale / ".git").write_text("gitdir: ...")

    meta = {
        "worktree_dir": str(wt_stale),
        "worktree_branch": "feat/stale",
    }

    # Simulate running inside wt_active
    from unittest.mock import patch
    with patch("pathlib.Path.cwd", return_value=wt_active):
        inherited = inherit_worktree_from_metadata(meta, repo_root=primary_repo)
        assert inherited is not None
        wt_dir, _ = inherited
        assert wt_dir.resolve() == wt_active.resolve()

    # When preserve_metadata_worktree is explicitly True, honor metadata
    meta["preserve_metadata_worktree"] = True
    with patch("pathlib.Path.cwd", return_value=wt_active):
        inherited = inherit_worktree_from_metadata(meta, repo_root=primary_repo)
        assert inherited is not None
        wt_dir, _ = inherited
        assert wt_dir.resolve() == wt_stale.resolve()


def test_rebind_worktree_metadata_paths_and_target_resolution(tmp_path):
    """Verify stale metadata deliverable and target output paths are rebound to active worktree."""
    primary_repo = tmp_path / "repo"
    primary_repo.mkdir()
    (primary_repo / ".git").mkdir()

    wt_active = primary_repo / ".worktrees" / "feat_active_task"
    wt_active.mkdir(parents=True)
    (wt_active / ".git").write_text("gitdir: ...")

    wt_stale = primary_repo / ".worktrees" / "live_stale_metadata"
    wt_stale.mkdir(parents=True)
    (wt_stale / ".git").write_text("gitdir: ...")

    meta = {
        "worktree_dir": str(wt_stale),
        "target_output_dir": str(wt_stale / "output" / "proj" / "10-16-2026" / "slug"),
        "output_01_path": str(wt_stale / "output" / "proj" / "10-16-2026" / "slug" / "01_transcript.md"),
        "output_02_path": str(wt_stale / "output" / "proj" / "10-16-2026" / "slug" / "02_recap.md"),
        "output_03_path": str(wt_stale / "output" / "proj" / "10-16-2026" / "slug" / "03_graph.json"),
    }

    from dual_pipeline.canonical_pipeline.worktree_inheritance import resolve_inherited_target_path
    from unittest.mock import patch
    with patch("pathlib.Path.cwd", return_value=wt_active):
        target_p = resolve_inherited_target_path(
            meta=meta,
            proj="proj",
            date_fn="10-16-2026",
            slug="slug",
            repo_root=primary_repo,
        )
        assert target_p is not None
        assert str(wt_active) in str(target_p)
        assert str(wt_stale) not in meta["target_output_dir"]
        assert str(wt_stale) not in meta["output_01_path"]
        assert str(wt_stale) not in meta["output_03_path"]
        assert str(wt_active) in meta["target_output_dir"]
        assert str(wt_active) in meta["output_03_path"]


def test_gate_1_3_date_normalization_resilience():
    """Verify Gate 1.3 temporal strictness passes with various input date formats normalized to MM/DD/YYYY."""
    from dual_pipeline.transcript_qa_core.forensic_healer import heal_header_date, _iso_to_us_date
    from dual_pipeline.transcript_stages_core.l1_attribution import assemble_native_hitl_transcript
    from dual_pipeline.transcript_qa_core.gate1 import _check_temporal_strictness

    # Check _iso_to_us_date conversions
    assert _iso_to_us_date("10-16-2026") == "10/16/2026"
    assert _iso_to_us_date("2026-10-16") == "10/16/2026"
    assert _iso_to_us_date("10/16/2026") == "10/16/2026"
    assert _iso_to_us_date("09-02-2026") == "09/02/2026"

    # Header healing from raw dashed date
    text_with_dashed = "# Strategy Sync -- HITL-Resolved Transcript | 10-16-2026\n\n**Date:** 10-16-2026\n\n**Alice:** Hello."
    healed = heal_header_date(text_with_dashed, target_date="10-16-2026", changes=[], audit_log=[])
    assert "**Date:** 10/16/2026" in healed

    # Assembly with existing header and dashed metadata
    meta = {"meeting_name": "Strategy Sync", "date": "10-16-2026", "attendees": ["Alice"]}
    assembled = assemble_native_hitl_transcript(text_with_dashed, metadata=meta, speaker_map={"Alice": "Alice"})
    assert "**Date:** 10/16/2026" in assembled
    assert "**Date:** 10-16-2026" not in assembled

    # Verify Gate 1.3 passes
    passed, header_date, iso_dates, failures = _check_temporal_strictness(assembled)
    assert passed is True
    assert header_date is True
    assert len(iso_dates) == 0
    assert len(failures) == 0


def test_resume_checkpoint_reanchors_out_p_to_active_worktree(tmp_path):
    """Verify that when resuming from an L1_L0 checkpoint created in a different worktree,
    pipeline._step_l1_l0_with_resume rebinds out_p and metadata to the active worktree."""
    primary_repo = tmp_path / "repo"
    primary_repo.mkdir()
    (primary_repo / ".git").mkdir()

    wt_active = primary_repo / ".worktrees" / "feat_active_task"
    wt_active.mkdir(parents=True)
    (wt_active / ".git").write_text("gitdir: ...")

    wt_stale = primary_repo / ".worktrees" / "live_stale_metadata"
    wt_stale.mkdir(parents=True)
    (wt_stale / ".git").write_text("gitdir: ...")

    stale_out = wt_stale / "output" / "proj" / "10-16-2026" / "slug"

    from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
    from dual_pipeline.checkpoint import PipelineCheckpointManager
    from unittest.mock import patch, MagicMock

    pipeline = CanonicalAgenticPipeline(provider_type="offline")
    checkpoint = PipelineCheckpointManager(stale_out)
    checkpoint.save_stage("L1_L0", {
        "modality": "AUDIO",
        "out_p": str(stale_out),
        "metadata": {"worktree_dir": str(wt_stale), "target_output_dir": str(stale_out)},
    })

    meta = {
        "worktree_dir": str(wt_stale),
        "target_output_dir": str(stale_out),
        "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
        "time": "01:00 PM - 01:30 PM ET",
    }
    tracker = MagicMock()

    with patch("pathlib.Path.cwd", return_value=wt_active):
        modality, out_p = pipeline._step_l1_l0_with_resume(
            checkpoint=checkpoint,
            should_resume=True,
            input_path=None,
            source_text="Sample text",
            meta=meta,
            output_dir="auto",
            tracker=tracker,
            progress_callback=None,
        )
        assert str(wt_active) in str(out_p)
        assert str(wt_stale) not in str(out_p)
        assert str(wt_active) in meta["target_output_dir"]
        assert str(wt_active) in meta["output_dir"]

