"""
tests/end_to_end/test_worktree_project_naming_e2e.py

End-to-End lifecycle tests for intelligent worktree naming, DocIQ project resolution,
output hierarchy verification, and roster attendee parsing.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.lifecycle import _resolve_project_name
from dual_pipeline.canonical_pipeline.worktree_naming import derive_worktree_identity
from dual_pipeline.document_pipeline_core.parallel_ingestor import ParallelDocumentIngestor
from dual_pipeline.meeting_paths import MeetingPathEnvelope
from dual_pipeline.parameters import get_known_projects, resolve_project_name
from dual_pipeline.query_router_core.project_router import determine_project_directory
from dual_pipeline.transcript_stages_core.graph_post_extractor import _ensure_roster_attendees
from tools.validate_output_hierarchy_core.helpers import _validate_project_affinity


def test_e2e_meeting_transcript_dociq_worktree_lifecycle():
    """E2E Test 1: Full lifecycle of DocIQ meeting transcript worktree and path resolution."""
    raw_title = "Dociq_Q4_Scope_Bailey_Amit"
    meeting_date = "09-30-2026"
    meta = {
        "title": raw_title,
        "meeting_name": raw_title,
        "date": meeting_date,
        "time": "0900AM",
        "project": "project_nexus",  # Stale container passed by caller
    }

    # 1. Derive worktree identity overrides container project with dociq
    wt_dir, branch = derive_worktree_identity(
        modality="MEETING_TRANSCRIPT",
        title=raw_title,
        project=meta["project"],
        date=meeting_date,
        clean_slug=raw_title,
    )
    assert branch == "transcript/dociq-09-30-2026-q4-scope-bailey-amit"
    assert "project-nexus" not in branch
    assert "project_nexus" not in str(wt_dir)

    # 2. Lifecycle resolve project name overrides container project
    resolved_proj = _resolve_project_name(meta, output_dir="auto")
    assert resolved_proj == "dociq"
    assert meta["project"] == "dociq"

    # 3. MeetingPathEnvelope creates canonical output structure under output/dociq/
    env = MeetingPathEnvelope.resolve(
        project_name=resolved_proj,
        meeting_name=raw_title,
        meeting_date=meeting_date,
        meeting_time="0900AM",
    )
    assert env.project_name == "dociq"
    assert f"output{Path('/').as_posix()}dociq" in str(env.output_dir)
    assert env.deliverable_01.name == "01_Dociq_Q4_Scope_Bailey_Amit_09-30-2026_0900AM_hitl_resolved_transcript.md"


def test_e2e_document_batch_parallel_dociq_ingest_resolution(tmp_path: Path):
    """E2E Test 2: Parallel document ingestion resolves individual document projects dynamically."""
    doc_file = tmp_path / "Collateral_Dociq_Q4_Plan_Charter_Scope.docx"
    doc_file.write_text("DocIQ Q4 Scope and Deliverables", encoding="utf-8")

    ingestor = ParallelDocumentIngestor(project="auto")
    assert ingestor.project == "auto"

    meta: dict[str, str] = {
        "title": doc_file.stem,
        "file_path": str(doc_file),
    }
    proj = determine_project_directory(meta)
    assert proj == "dociq"


def test_e2e_output_hierarchy_detects_misplaced_dociq_artifact():
    """E2E Test 3: Output hierarchy validator flags DocIQ deliverable misplaced in project_nexus."""
    # Misplaced in project_nexus -> must flag mismatch
    err = _validate_project_affinity("Collateral_Dociq_Q4_Plan_Charter_Scope", "project_nexus", "output/project_nexus/10-01-2026")
    assert err is not None
    assert "[PROJECT_AFFINITY_MISMATCH]" in err
    assert "dociq" in err

    # Placed in canonical dociq directory -> passes cleanly
    ok = _validate_project_affinity("Collateral_Dociq_Q4_Plan_Charter_Scope", "dociq", "output/dociq/10-01-2026")
    assert ok is None


def test_e2e_roster_attendee_org_vs_role_separation():
    """E2E Test 4: Roster parsing creates person and org, never role:partner_advisory."""
    mutations: dict = {}
    metadata = {
        "attendees": ["Bailey Morton (PartnerAdvisory)", "Amit Ayer (PartnerAdvisory)"],
        "source_text": "Meeting discussion on DocIQ architecture.",
    }
    _ensure_roster_attendees(mutations, metadata)

    entities = mutations.get("entity_upserts", [])
    entity_ids = {e.get("id") for e in entities if isinstance(e, dict)}

    assert "person:bailey_morton" in entity_ids
    assert "person:amit_ayer" in entity_ids
    assert "org:partner_advisory" in entity_ids
    assert "role:partner_advisory" not in entity_ids
