"""
tests/test_attachment_vision_replay_e2e.py

Class B Receipt & E2E Verification Suite for Wave 3:
Attachment Staging, Vision Cache Reuse & Walkthrough Alignment Parity.

Verifies:
1. All 4 meeting screenshots are staged into output/ referenced_artifacts/ with correct relative paths.
2. Drops at 00:00:00 in collateral_drop_log.json maintain COLLATERAL_REVIEWED attribution.
3. Stale foreign worktree paths in metadata["attachments"] fall back cleanly to local artifacts without wiping.
4. Cached vision extractions (components, claims, tables, summary) from metadata.json are reused deterministically
   without triggering external vision API calls or failing in hermetic environments.
5. Spoken walkthrough turns correlate with prototype screenshots and maintain authenticated attribution.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.attachment_core.detector import discover_attachments, stage_attachments_into_output
from dual_pipeline.attachment_core.review_provenance import is_authenticated_drop
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import ingest_and_align_attachments


@pytest.fixture
def bcg_artifacts_sandbox(tmp_path: Path) -> Dict[str, Any]:
    """Sets up an isolated sandbox simulating the 09/28/2026 BCG meeting container."""
    container = tmp_path / "output" / "nea" / "09-28-2026" / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter"
    ref_dir = container / "referenced_artifacts"
    ref_dir.mkdir(parents=True, exist_ok=True)

    png_names = [
        "neet_insights_hub_beginning_of_shift_prototype.png",
        "neet_insights_hub_pano_ai_smoke_detection.png",
        "neet_insights_hub_create_event_drawer.png",
        "neet_insights_hub_live_data_active_events.png",
    ]

    png_files = []
    # Create valid minimal 1x1 PNG binaries with unique header comments
    minimal_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    drop_log_entries = []
    attachments_cached = []

    for idx, fname in enumerate(png_names, 1):
        fpath = ref_dir / fname
        # Pad with index byte to produce distinct sha256 digests
        fpath.write_bytes(minimal_png + bytes([idx]))
        png_files.append(fpath)

        import hashlib
        sha = hashlib.sha256(fpath.read_bytes()).hexdigest()

        drop_log_entries.append({
            "filename": fname,
            "path": f"referenced_artifacts/{fname}",
            "dropped_at_elapsed": "00:00:00",
            "sha256": sha,
            "attribution_mode": "COLLATERAL_REVIEWED",
            "source": "copilot_drop",
        })

        attachments_cached.append({
            "attachment_id": f"att-{idx:02d}-{fname[:20]}",
            "filename": fname,
            "original_path": f"/foreign/stale/worktree/path/referenced_artifacts/{fname}",
            "staged_path": f"/foreign/stale/worktree/path/referenced_artifacts/{fname}",
            "relative_staged_path": f"referenced_artifacts/{fname}",
            "sha256": sha,
            "file_size_bytes": fpath.stat().st_size,
            "mime_type": "image/png",
            "attribution_mode": "COLLATERAL_REVIEWED",
            "dropped_at_elapsed": "00:00:00",
            "title": fname.replace("_", " ").replace(".png", "").title(),
            "summary": f"Prototype interface showing {fname}.",
            "extracted_text": f"APEX_TRANS Insights Hub prototype screen {fname} with active controls.",
            "components": [
                {"component_ref": f"comp-{idx}-01", "label": "Shift Status", "type": "system_component"},
                {"component_ref": f"comp-{idx}-02", "label": "Pano AI Smoke Detection", "type": "system_component"},
            ],
            "claims": [
                {"claim": f"Visual prototype {fname} demonstrates real-time alert aggregation."},
            ],
            "tables": [],
        })

    drop_log_file = container / "collateral_drop_log.json"
    drop_log_file.write_text(json.dumps(drop_log_entries, indent=2), encoding="utf-8")

    transcript_text = (
        "# Live Meeting Transcript: introduction to PartnerAdvisory/BCG\n\n"
        "**Date:** 09/28/2026\n"
        "**Start Time:** 03:09 PM ET\n\n"
        "**[00:02]** Marcus Vance: Let's pull up the beginning of shift prototype on the screen.\n\n"
        "**[00:08]** Marcus Vance: Here we can see the Pano AI smoke detection alerts integrated into the active events drawer.\n\n"
        "**[00:15]** Will Booth: Looking at the live data active events drawer, this aligns with what BCG anticipated.\n\n"
        "**[00:22]** Alex Rivera: That cross-group alignment and shared belief in program value is exactly what we need.\n"
    )
    transcript_file = container / "01_live_transcript.md"
    transcript_file.write_text(transcript_text, encoding="utf-8")

    metadata = {
        "project": "nea",
        "date": "09/28/2026",
        "date_filename": "09-28-2026",
        "time": "03:09 PM - 03:33 PM ET",
        "container_dir": str(container),
        "output_dir": str(container),
        "attachments": attachments_cached,
    }

    return {
        "container": container,
        "ref_dir": ref_dir,
        "png_files": png_files,
        "drop_log_file": drop_log_file,
        "transcript_file": transcript_file,
        "transcript_text": transcript_text,
        "metadata": metadata,
        "png_names": png_names,
    }


def test_zero_elapsed_drop_log_provenance():
    """Scenario 1: Authenticated drop evaluation preserves 00:00:00 drops with COLLATERAL_REVIEWED mode."""
    drop_entry = {
        "filename": "neet_insights_hub_beginning_of_shift_prototype.png",
        "path": "referenced_artifacts/neet_insights_hub_beginning_of_shift_prototype.png",
        "dropped_at_elapsed": "00:00:00",
        "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "attribution_mode": "COLLATERAL_REVIEWED",
    }
    assert is_authenticated_drop(drop_entry) is True, "00:00:00 drops with COLLATERAL_REVIEWED must be authenticated"

    # Synthetic or background drops must remain rejected
    synthetic_drop = dict(drop_entry, is_synthetic=True)
    assert is_authenticated_drop(synthetic_drop) is False

    background_drop = dict(drop_entry, attribution_mode="BACKGROUND")
    assert is_authenticated_drop(background_drop) is False


def test_stale_worktree_path_fallback_discovery(bcg_artifacts_sandbox: Dict[str, Any]):
    """Scenario 2: Stale cross-worktree paths gracefully resolve to co-located referenced_artifacts/ files."""
    box = bcg_artifacts_sandbox
    stale_attachment_paths = [
        "/nonexistent_machine_dir/worktrees/old_branch/output/nea/09-28-2026/Introduction/referenced_artifacts/neet_insights_hub_beginning_of_shift_prototype.png",
        "/nonexistent_machine_dir/worktrees/old_branch/output/nea/09-28-2026/Introduction/referenced_artifacts/neet_insights_hub_pano_ai_smoke_detection.png",
    ]

    discovered = discover_attachments(
        attachment_paths=stale_attachment_paths,
        meeting_output_dir=str(box["container"]),
        transcript_path=str(box["transcript_file"]),
    )

    assert len(discovered) == 2, f"Expected 2 recovered files from fallback, got {len(discovered)}"
    discovered_names = {p.name for p in discovered}
    assert "neet_insights_hub_beginning_of_shift_prototype.png" in discovered_names
    assert "neet_insights_hub_pano_ai_smoke_detection.png" in discovered_names


def test_attachment_staging_and_drop_log_sync(bcg_artifacts_sandbox: Dict[str, Any]):
    """Scenario 3: stage_attachments_into_output populates referenced_artifacts/ and preserves COLLATERAL_REVIEWED."""
    box = bcg_artifacts_sandbox
    target_out = box["container"] / "new_stage_target"
    target_out.mkdir()

    records = stage_attachments_into_output(box["png_files"], target_out)
    assert len(records) == 4, f"Expected 4 staged records, got {len(records)}"

    for rec in records:
        assert rec.attribution_mode == "COLLATERAL_REVIEWED"
        assert rec.relative_staged_path.startswith("referenced_artifacts/")
        assert (target_out / rec.relative_staged_path).is_file()
        assert rec.dropped_at_elapsed == "00:00:00"

    # Drop log must be synced to destination
    dest_drop_log = target_out / "collateral_drop_log.json"
    assert dest_drop_log.is_file(), "collateral_drop_log.json must be synced to output directory"
    log_data = json.loads(dest_drop_log.read_text(encoding="utf-8"))
    assert len(log_data) == 4
    assert all(e.get("attribution_mode") == "COLLATERAL_REVIEWED" for e in log_data)


def test_vision_cache_reuse_and_walkthrough_alignment_e2e(bcg_artifacts_sandbox: Dict[str, Any]):
    """Scenario 4: Full ingest_and_align_attachments reuses cached vision data without calling vision APIs."""
    box = bcg_artifacts_sandbox
    metadata = dict(box["metadata"])

    updated_transcript = ingest_and_align_attachments(
        metadata=metadata,
        transcript_text=box["transcript_text"],
        output_dir=str(box["container"]),
        input_path=str(box["transcript_file"]),
    )

    # Ingested attachments in metadata
    attachments = metadata.get("attachments")
    assert isinstance(attachments, list)
    assert len(attachments) == 4, f"Expected 4 attachments in metadata, got {len(attachments)}"

    for att in attachments:
        assert att.get("attribution_mode") == "COLLATERAL_REVIEWED"
        assert att.get("relative_staged_path", "").startswith("referenced_artifacts/")
        # Ensure cached components and claims were preserved without re-extraction
        comps = att.get("components", [])
        assert len(comps) >= 2, f"Expected cached components in {att.get('filename')}"
        claims = att.get("claims", [])
        assert len(claims) >= 1, f"Expected cached claims in {att.get('filename')}"
        assert "Prototype interface" in att.get("summary", "")

    # Spoken walkthrough turns must be aligned
    alignment = metadata.get("attachment_alignment")
    assert alignment is not None, "attachment_alignment must be populated in metadata"
    mentions = alignment.get("mentions", [])
    assert len(mentions) >= 2, f"Expected >= 2 attachment mentions from walkthrough turns, got {len(mentions)}"
    turn_aligns = alignment.get("turn_alignments", {})
    assert len(turn_aligns) >= 2, f"Expected >= 2 aligned turns, got {len(turn_aligns)}"

    # Annotations must be injected into transcript
    assert "<!-- [COLLATERAL_ATTACHMENT:" in updated_transcript or "neet_insights_hub_" in updated_transcript
