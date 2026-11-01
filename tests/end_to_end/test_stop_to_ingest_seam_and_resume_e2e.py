"""
tests/end_to_end/test_stop_to_ingest_seam_and_resume_e2e.py

End-to-end verification for Wave 7:
1. Worktree inheritance & relative path resolution across --worktree-dir.
2. Co-hydration of speaker_hints.json and *.voiceprints.json in Stage U0.
3. Speaker binding digest invalidation on --resume (unfreezing Output 01 and remapping resolved_transcript).
4. Exclusion of *live_transcript*.md from Output 01 checkpoint globs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.attachments import (
    _has_required_deliverable_checkpoints,
    try_resume_passing_attempt,
)
from dual_pipeline.canonical_pipeline.c0_pa_runner import (
    compute_speaker_binding_digest,
    step_c0_pa_with_resume,
)
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _memoize_output_1
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.u0_live_artifacts import hydrate_live_container_artifacts
from dual_pipeline.canonical_pipeline.worktree_inheritance import inherit_worktree_from_metadata
from dual_pipeline.cli.ingest import build_canonical_metadata


def test_worktree_dir_precedence_and_u0_cohydration_e2e(tmp_path: Path) -> None:
    wt_dir = tmp_path / ".worktrees" / "feat_live_nea_10_29"
    container_dir = wt_dir / "output" / "nea" / "10-29-2026" / "Architecture_Sync"
    cache_dir = container_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    wav_p = container_dir / "00_10-29-2026_Architecture_Sync_master.wav"
    wav_p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")

    manifest = {
        "project": "nea",
        "meeting": "Architecture Sync",
        "date": "10/29/2026",
        "attendees": ["Amit Ayer (PartnerAdvisory)", "Daniel Sabet (Apex Global)"],
    }
    (container_dir / "session_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # 1 hint for SPEAKER_00 -> Amit Ayer
    hints = [{"voice_id": "SPEAKER_00", "speaker_name": "Amit Ayer", "quote": "Let us review the pipeline."}]
    (container_dir / "speaker_hints.json").write_text(json.dumps(hints), encoding="utf-8")

    # Voiceprints sidecar has SPEAKER_01 -> Daniel Sabet
    vp_data = {
        "audio_sha256": "deadbeef" * 8,
        "matches": {"SPEAKER_01": "Daniel Sabet"},
        "centroids": {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]},
    }
    (cache_dir / "00_10-29-2026_Architecture_Sync_master.voiceprints.json").write_text(
        json.dumps(vp_data), encoding="utf-8"
    )

    rel_input = Path("output/nea/10-29-2026/Architecture_Sync/00_10-29-2026_Architecture_Sync_master.wav")
    args = argparse.Namespace(
        input=str(rel_input),
        source=None,
        worktree_dir=str(wt_dir),
        worktree_branch="feat/live-nea-10-29",
        metadata=None,
        project="nea",
        meeting="Architecture Sync",
        date="10/29/2026",
        attendees=None,
        speaker_map=None,
        entity_resolutions=None,
        resume=False,
        force_rerun=False,
        document=False,
        notes=False,
        copilot_notes=False,
        output3_only=False,
        bypass_speaker_hitl=False,
        allow_duplicate=False,
    )
    meta = build_canonical_metadata(args)
    assert meta["preserve_metadata_worktree"] is True
    assert Path(meta["input_path"]).resolve() == wav_p.resolve()

    inherited = inherit_worktree_from_metadata(meta, repo_root=tmp_path)
    assert inherited is not None
    assert inherited[0].resolve() == wt_dir.resolve()

    hydrate_live_container_artifacts(meta, input_path=wav_p)
    # Both SPEAKER_00 (from hint) and SPEAKER_01 (from voiceprints) must be present!
    assert meta["speaker_map"].get("SPEAKER_00") == "Amit Ayer"
    assert meta["speaker_map"].get("SPEAKER_01") == "Daniel Sabet"
    assert meta["voiceprint_matches"].get("SPEAKER_01") == "Daniel Sabet"


def test_resume_speaker_binding_change_unfreezes_output_1_and_remaps_transcript_e2e(tmp_path: Path) -> None:
    out_p = tmp_path / "output" / "nea" / "10-29-2026" / "Sync"
    out_p.mkdir(parents=True, exist_ok=True)

    old_map = {"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Unknown Speaker"}
    old_meta = {"speaker_map": old_map, "speaker_hints": [], "voiceprint_matches": {}}
    old_sha = compute_speaker_binding_digest(old_meta)

    initial_resolved = "**[00:00:05] Amit Ayer:** Welcome everyone.\n**[00:00:10] Unknown Speaker:** Thanks Amit."
    ckpt = MagicMock()
    ckpt.has_stage.return_value = True
    ckpt.get_stage.return_value = {
        "pa_context": {"metadata": {"speaker_map": dict(old_map)}},
        "source_text": initial_resolved,
        "resolved_transcript": initial_resolved,
        "speaker_map": dict(old_map),
        "speaker_hints": [],
        "voiceprint_matches": {},
        "binding_sha256": old_sha,
    }

    # Now operator supplies updated speaker_map resolving SPEAKER_01 -> Daniel Sabet on --resume
    resumed_meta = {
        "resume": True,
        "speaker_map": {"SPEAKER_00": "Amit Ayer", "SPEAKER_01": "Daniel Sabet"},
        "speaker_hints": [],
        "voiceprint_matches": {"SPEAKER_01": "Daniel Sabet"},
    }
    tracker = MagicMock()
    pa_ctx, updated_text = step_c0_pa_with_resume(
        pipeline=MagicMock(),
        checkpoint=ckpt,
        should_resume=True,
        modality=CanonicalModality.AUDIO,
        source_text=initial_resolved,
        input_hash="hash123",
        meta=resumed_meta,
        presentation_path=None,
        out_p=out_p,
        tracker=tracker,
    )

    assert resumed_meta.get("speaker_binding_updated_on_resume") is True
    assert resumed_meta["binding_sha256"] != old_sha
    assert "Daniel Sabet" in updated_text
    assert "Unknown Speaker" not in updated_text

    # Verify _memoize_output_1 refuses to freeze stale Output 01 when speaker_binding_updated_on_resume is True
    stale_01 = out_p / "01_10-29-2026_Sync_hitl_resolved_transcript.md"
    stale_01.write_text(initial_resolved + "\n" + ("Padding line for size threshold.\n" * 10), encoding="utf-8")
    sm = MagicMock()
    sm.is_frozen.return_value = False
    memoized = _memoize_output_1(out_p, sm, resumed_meta, regen_spec="")
    assert memoized is False
    sm.unfreeze_artifact.assert_called_once_with("output_1", force=True)
    assert try_resume_passing_attempt(out_p, resumed_meta, CanonicalModality.AUDIO) is None


def test_live_transcript_excluded_from_output_01_checkpoint_globs_e2e(tmp_path: Path) -> None:
    att_dir = tmp_path / "attempts" / "attempt_1"
    att_dir.mkdir(parents=True, exist_ok=True)
    # Write a 01_*_live_transcript.md (NOT hitl_resolved_transcript.md)
    (att_dir / "01_10-29-2026_Sync_live_transcript.md").write_text(
        "**[00:00:01] Voice A:** Raw live stream text.\n" * 15, encoding="utf-8"
    )
    (att_dir / "02_10-29-2026_Sync_executive_recap.md").write_text("# Recap\n" * 30, encoding="utf-8")
    (att_dir / "03_10-29-2026_Sync_canonical_signal_projection.json").write_text("{}", encoding="utf-8")

    assert _has_required_deliverable_checkpoints(att_dir, CanonicalModality.AUDIO) is False
