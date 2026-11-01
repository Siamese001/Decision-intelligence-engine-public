"""
tests/unit/test_transcript_worktree_and_outputs.py

Unit tests for:
1. Dedicated transcript worktree and branch prefix governance (INV-WT-04 / INV-WT-03)
2. Raw transcript 3-output enforcement (01, 02, 03 deliverables required)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    DocumentSynthesisStrategy,
    FullMeetingSynthesisStrategy,
    SynthesisResult,
)
from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables


def test_transcript_worktree_naming_derivation():
    """Verify that transcript modalities map to transcript/ branch and .worktrees/transcript_ directory."""
    root = Path("/dummy/repo")
    modalities = [
        CanonicalModality.MEETING_TRANSCRIPT,
        "MEETING_TRANSCRIPT",
        "TRANSCRIPT",
        "RAW_TRANSCRIPT",
        "MEETING",
    ]
    for mod in modalities:
        wt_dir, branch = derive_worktree_identity(
            modality=mod,
            title="Working Session LENS Testing Validation",
            repo_root=root,
        )
        assert branch.startswith("transcript/"), f"Expected transcript/ branch for {mod}, got {branch}"
        assert wt_dir.name.startswith("transcript_"), f"Expected transcript_ dir for {mod}, got {wt_dir.name}"
        assert CONVENTIONAL_BRANCH_RE.match(branch) is not None, f"Branch '{branch}' failed CONVENTIONAL_BRANCH_RE"


def test_document_ingestion_remains_distinct():
    """Verify that document ingestion retains ingest/doc- prefix and does not collide with transcript."""
    root = Path("/dummy/repo")
    wt_dir, branch = derive_worktree_identity(
        modality=CanonicalModality.DOCUMENT,
        title="Product Owner Role Description",
        repo_root=root,
    )
    assert branch.startswith("ingest/doc-")
    assert wt_dir.name.startswith("ingest_doc_")
    assert CONVENTIONAL_BRANCH_RE.match(branch) is not None


def test_document_strategy_rejects_raw_vtt_transcript():
    """Verify that DocumentSynthesisStrategy fails closed when given a raw VTT transcript."""
    provider = MagicMock()
    strategy = DocumentSynthesisStrategy(provider)
    vtt_text = "WEBVTT\n\n00:00:03.460 --> 00:00:07.957\n<v Speaker>Hello world</v>\n"

    with pytest.raises(ValueError, match="DocumentSynthesisStrategy cannot process raw transcript"):
        strategy.synthesize(
            source_text=vtt_text,
            pa_context={},
            metadata={},
        )


def test_full_meeting_strategy_requires_three_deliverables():
    """Verify that FullMeetingSynthesisStrategy declares deliverables 01, 02, and 03."""
    provider = MagicMock()
    strategy = FullMeetingSynthesisStrategy(provider)
    assert strategy.required_deliverables == ["01", "02", "03"]


def test_deploy_deliverables_enforces_three_outputs_for_transcript(tmp_path: Path):
    """Verify that deploy_deliverables raises RuntimeError if any of 01, 02, 03 are missing for transcripts."""
    out_p = tmp_path / "output"
    out_p.mkdir()
    meta = {
        "meeting_name": "LENS Testing",
        "date": "10/09/2026",
    }

    # Missing output 01
    res_missing_1 = SynthesisResult(output_1=None, output_2="Recap text", output_3="{}")
    with pytest.raises(RuntimeError, match="Missing required deliverable.*01_hitl_resolved_transcript"):
        deploy_deliverables(out_p, meta, CanonicalModality.MEETING_TRANSCRIPT, res_missing_1)

    # Missing output 02
    res_missing_2 = SynthesisResult(output_1="Transcript text", output_2=None, output_3="{}")
    with pytest.raises(RuntimeError, match="Missing required deliverable.*02_executive_recap"):
        deploy_deliverables(out_p, meta, CanonicalModality.MEETING_TRANSCRIPT, res_missing_2)

    # Missing output 03
    res_missing_3 = SynthesisResult(output_1="Transcript text", output_2="Recap text", output_3=None)
    with pytest.raises(RuntimeError, match="Missing required deliverable.*03_canonical_signal_projection"):
        deploy_deliverables(out_p, meta, CanonicalModality.MEETING_TRANSCRIPT, res_missing_3)

    # All three present succeeds
    res_complete = SynthesisResult(output_1="Transcript text", output_2="Recap text", output_3="{}")
    artifacts = deploy_deliverables(out_p, meta, CanonicalModality.MEETING_TRANSCRIPT, res_complete)
    assert "01_transcript" in artifacts
    assert "02_recap" in artifacts
    assert "03_graph" in artifacts
