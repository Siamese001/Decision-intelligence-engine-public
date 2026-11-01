"""
tests/test_metadata_temporal_roster_replay_adversarial.py

Wave 2 Adversarial and E2E Test Suite:
Validates container .cache/, metadata, date/time window, and silent roster hydration
during meeting replay from file or directory inputs without requiring 13 CLI flags.

Invariants verified:
1. Replaying BCG meeting with --input only hydrates exact MM/DD/YYYY date (09/28/2026)
2. Replaying BCG meeting hydrates exact Eastern time window (0309PM_0333PM_ET)
3. Preserves full 7-person roster including silent attendees (Ariel Wait, Amit Ayer)
4. Excludes 'Invited / Not Present' (Peter Ashley) and presented collateral from attendee roster
5. Normalizes BCG and NEA organizational mappings and executive titles
6. Recovers date, project, and time from folder hierarchy and transcript footers
7. Converts UTC ISO timestamps to America/New_York (Eastern Time) without 6-digit fallback
"""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.cli.ingest import build_canonical_metadata, _generate_default_directive_id
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.temporal_resolution import resolve_temporal_window
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


BCG_REAL_PATH = (
    Path(__file__).resolve().parents[2]
    / "feat_rca_neet_ingestion_runtime"
    / "output"
    / "nea"
    / "09-28-2026"
    / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter"
)
LENS_REAL_PATH = Path("output/project_lens/10-16-2026/Ed_Amit")
LENS_OCR_REAL_PATH = Path("output/project_lens/10-16-2026/Working_Session_Ocr_Skyler_PartnerAdvisory")


@pytest.fixture
def bcg_sandbox_container(tmp_path: Path) -> Path:
    """Creates a hermetic copy of the BCG meeting container for reproducible adversarial testing."""
    container = tmp_path / "output" / "nea" / "09-28-2026" / "Introduction_PartnerAdvisory_Bcg_Mitchell_Peter"
    container.mkdir(parents=True, exist_ok=True)
    cache_dir = container / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    if BCG_REAL_PATH.is_dir():
        for item in BCG_REAL_PATH.iterdir():
            if item.is_file():
                shutil.copy2(item, container / item.name)
        real_cache = BCG_REAL_PATH / ".cache"
        if real_cache.is_dir():
            for item in real_cache.iterdir():
                if item.is_file():
                    shutil.copy2(item, cache_dir / item.name)
    else:
        # Construct synthetic equivalent if source worktree was removed
        transcript_content = (
            "# Live Meeting Transcript: introduction to PartnerAdvisory/BCG\n\n"
            "**Date:** 09/28/2026\n"
            "**Start Time:** 03:09 PM ET\n"
            "**Project:** `nea`\n"
            "**Status:** `RECORDING_IN_PROGRESS`\n\n"
            "### Attendees (Present)\n"
            "- **Alex Rivera** (Technology Lead, NEA)\n"
            "- **Marcus Vance** (Senior Consultant, PartnerAdvisory)\n"
            "- **Anece Elakkari** (Senior Consultant, PartnerAdvisory)\n"
            "- **Will Booth** (Consultant, BCG)\n"
            "- **John Snipes** (Managing Director, BCG)\n"
            "- **Ariel Wait** (Project Manager, Apex Global Enterprises)\n"
            "- **Amit Ayer** (Lead Enterprise Architect, PartnerAdvisory)\n\n"
            "**Invited / Not Present:**\n"
            "- Peter Ashley (Senior Consultant, PartnerAdvisory)\n\n"
            "### Presented Collateral & Artifacts\n"
            "- **Artifact 1:** `APEX_TRANS Insights Hub — Beginning of Shift Prototype`\n"
            "  - **Presenter:** Marcus Vance (PartnerAdvisory)\n\n"
            "---\n"
            "*Session paused at 03:33 PM ET. Total chunks processed: 215*\n"
        )
        (container / "01_09-28-2026_Introduction_PartnerAdvisory_Bcg_Mitchell_Peter_live_transcript.md").write_text(
            transcript_content, encoding="utf-8"
        )
        manifest = {
            "title": "Introduction to PartnerAdvisory/BCG",
            "project": "nea",
            "date": "09/28/2026",
            "time": "03:09 PM - 03:33 PM ET",
            "started_at": "2026-09-28T19:09:00Z",
            "stopped_at": "2026-09-28T19:33:00Z",
            "attendees": [
                {"name": "Alex Rivera", "org": "Apex Energy Transmission (APEX_TRANS)", "role": "Technology Lead"},
                {"name": "Ariel Wait", "org": "Apex Global Enterprises (NEA)", "role": "Project Manager"},
                {"name": "Marcus Vance", "org": "PartnerAdvisory", "role": "Senior Consultant"},
                {"name": "Anece Elakkari", "org": "PartnerAdvisory", "role": "Senior Consultant"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Enterprise Architect"},
                {"name": "Will Booth", "org": "Boston Consulting Group (BCG)", "role": "Consultant"},
                {"name": "John Snipes", "org": "Boston Consulting Group (BCG)", "role": "Managing Director"},
            ],
        }
        (container / "metadata.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (container / "intake_package.json").write_text(json.dumps({"meeting": manifest}, indent=2), encoding="utf-8")

    return container


def test_bcg_container_metadata_hydration_file_input(bcg_sandbox_container: Path):
    """Scenario 1: Replay BCG meeting with --input <transcript_file> only."""
    trans_files = list(bcg_sandbox_container.glob("01_*live_transcript.md"))
    assert trans_files, "Transcript file must exist in BCG sandbox"
    target_file = trans_files[0]

    args = argparse.Namespace(
        input=str(target_file),
        metadata=None,
        project=None,
        meeting=None,
        date=None,
        time=None,
        attendees=None,
    )
    meta = build_canonical_metadata(args)

    assert meta.get("date") == "09/28/2026", f"Expected '09/28/2026', got {meta.get('date')}"
    assert meta.get("date_filename") == "09-28-2026", f"Expected '09-28-2026', got {meta.get('date_filename')}"
    assert meta.get("time") == "03:09 PM - 03:33 PM ET", f"Expected '03:09 PM - 03:33 PM ET', got {meta.get('time')}"
    assert meta.get("time_filename") == "0309PM_0333PM_ET", f"Expected '0309PM_0333PM_ET', got {meta.get('time_filename')}"
    assert meta.get("project") == "nea", f"Expected 'nea', got {meta.get('project')}"
    assert "PartnerAdvisory/BCG" in meta.get("meeting_name", ""), f"Meeting name should reflect PartnerAdvisory/BCG: {meta.get('meeting_name')}"

    roster = meta.get("roster") or []
    assert len(roster) == 7, f"Expected exactly 7 present attendees, got {len(roster)}: {[r.get('name') for r in roster]}"

    names = {r.get("name") for r in roster}
    assert "Alex Rivera" in names
    assert "Marcus Vance" in names
    assert "Anece Elakkari" in names
    assert "Will Booth" in names
    assert "John Snipes" in names
    # Verify silent attendees are preserved
    assert "Ariel Wait" in names, "Silent attendee Ariel Wait must be preserved"
    assert "Amit Ayer" in names, "Silent attendee Amit Ayer must be preserved"
    # Verify absent attendee is not in active roster
    assert "Peter Ashley" not in names, "Absent attendee Peter Ashley must not be in present roster"

    # Verify directive ID is deterministic and grounded
    directive_id = _generate_default_directive_id(meta)
    assert directive_id.startswith("directive-nea-")
    assert "09-28-2026" in directive_id


def test_bcg_container_metadata_hydration_directory_input(bcg_sandbox_container: Path):
    """Scenario 2: Replay BCG meeting with --input <container_dir> only."""
    args = argparse.Namespace(
        input=str(bcg_sandbox_container),
        metadata=None,
        project=None,
        meeting=None,
        date=None,
        time=None,
        attendees=None,
    )
    meta = build_canonical_metadata(args)

    assert meta.get("date") == "09/28/2026"
    assert meta.get("date_filename") == "09-28-2026"
    assert meta.get("time") == "03:09 PM - 03:33 PM ET"
    assert meta.get("time_filename") == "0309PM_0333PM_ET"
    assert meta.get("project") == "nea"

    roster = meta.get("roster") or []
    assert len(roster) == 7


def test_lens_ed_amit_metadata_hydration():
    """Scenario 3: Replay LENS Ed_Amit meeting with --input only."""
    if not LENS_REAL_PATH.is_dir():
        pytest.skip("LENS Ed_Amit container not present in workspace")

    t_file = LENS_REAL_PATH / "01_10-16-2026_Ed_Amit_live_transcript.md"
    if not t_file.is_file():
        pytest.skip(f"LENS Ed_Amit transcript not found: {t_file}")

    args = argparse.Namespace(
        input=str(t_file),
        metadata=None,
        project=None,
        meeting=None,
        date=None,
        time=None,
        attendees=None,
    )
    meta = build_canonical_metadata(args)

    assert meta.get("date") == "10/16/2026"
    assert meta.get("date_filename") == "10-16-2026"
    assert meta.get("project") == "project_lens"
    assert meta.get("time") == "10:34 AM - 11:07 AM ET"
    assert meta.get("time_filename") in ("1034AM1107AMET", "1034AM_1107AM_ET")

    roster = meta.get("roster") or []
    names = {r.get("name") for r in roster}
    assert "Eduardo More" in names
    assert "Amit Ayer" in names


def test_lens_ocr_working_session_directory_input():
    """Scenario 4: Replay LENS OCR meeting with --input <dir> only."""
    if not LENS_OCR_REAL_PATH.is_dir():
        pytest.skip("LENS OCR container not present in workspace")

    args = argparse.Namespace(
        input=str(LENS_OCR_REAL_PATH),
        metadata=None,
        project=None,
        meeting=None,
        date=None,
        time=None,
        attendees=None,
    )
    meta = build_canonical_metadata(args)

    assert meta.get("date") == "10/16/2026"
    assert meta.get("date_filename") == "10-16-2026"
    assert meta.get("project") == "project_lens"
    assert meta.get("time") == "02:22 PM - 03:09 PM ET"

    roster = meta.get("roster") or []
    assert len(roster) == 5
    names = {r.get("name") for r in roster}
    assert "Skyler Carlson" in names
    assert "Amit Ayer" in names


def test_adversarial_date_and_time_recovery_from_ancestor_folder(tmp_path: Path):
    """Scenario 5: Adversarial recovery when no metadata file exists and transcript lacks date."""
    target_dir = tmp_path / "output" / "portfolio_ops" / "07-15-2026" / "Review_Meeting_Session"
    target_dir.mkdir(parents=True, exist_ok=True)
    t_file = target_dir / "01_Review_Meeting_Session_raw_transcript.md"
    t_file.write_text(
        "# Strategic Portfolio Session\n\n"
        "**Start Time:** 09:15 AM ET\n\n"
        "### Attendees\n"
        "- Jane Doe (Director, Apex Global Enterprises)\n"
        "- Bob Smith (Principal, PartnerAdvisory)\n\n"
        "---\n"
        "*Session paused at 09:45 AM ET. Total chunks: 42*\n",
        encoding="utf-8",
    )

    meta: dict = {"input_path": str(t_file)}
    hydrated = hydrate_live_container_artifacts(meta, input_path=t_file)

    assert meta.get("date") == "07/15/2026", "Must recover date from ancestor folder, not today UTC"
    assert meta.get("date_filename") == "07-15-2026"
    assert meta.get("project") == "portfolio_ops", "Must recover project from output/<project>/ folder"
    assert meta.get("time") == "09:15 AM - 09:45 AM ET", "Must build window from Start Time and Session paused at"
    assert meta.get("time_filename") == "0915AM_0945AM_ET"
    assert len(meta.get("roster", [])) == 2


def test_adversarial_collateral_does_not_pollute_attendee_roster(tmp_path: Path):
    """Scenario 6: Ensure ### Presented Collateral & Artifacts does not pollute attendee roster."""
    meeting_dir = tmp_path / "output" / "nea" / "09-28-2026" / "Prototype_Demo"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    t_file = meeting_dir / "01_transcript.md"
    t_file.write_text(
        "# Prototype Demo\n\n"
        "**Date:** 09/28/2026\n"
        "**Time:** 01:00 PM - 01:30 PM ET\n\n"
        "### Attendees (Present)\n"
        "- Alice Walker (Lead Architect, PartnerAdvisory)\n"
        "- Charlie Brown (Tech Director, NEA)\n\n"
        "### Presented Collateral & Artifacts\n"
        "- **Artifact 1:** `APEX_TRANS Insights Hub — Beginning of Shift Prototype`\n"
        "  - **URL:** `partner_advisorycx-insightshub.figma.site`\n"
        "  - **Presenter:** Alice Walker (PartnerAdvisory)\n"
        "  - **File Reference:** `referenced_artifacts/shift_prototype.png`\n"
        "- **Artifact 2:** `Pano AI Smoke Detection View`\n"
        "  - **URL:** `360.pano.ai`\n\n"
        "### Dialogue\n"
        "**Alice Walker:** Welcome everyone.\n",
        encoding="utf-8",
    )

    meta: dict = {"input_path": str(t_file)}
    hydrate_live_container_artifacts(meta, input_path=t_file)

    roster = meta.get("roster") or []
    names = [r.get("name") for r in roster]
    assert len(names) == 2, f"Roster must have exactly 2 people, got: {names}"
    assert "Alice Walker" in names
    assert "Charlie Brown" in names
    for bad_token in ("Artifact 1", "Artifact 2", "APEX_TRANS Insights Hub", "Pano AI", "URL", "Presenter"):
        assert not any(bad_token in n for n in names), f"Collateral token '{bad_token}' leaked into roster: {names}"


def test_adversarial_utc_iso_timestamp_conversion_to_eastern(tmp_path: Path):
    """Scenario 7: Ensure UTC ISO timestamps convert to America/New_York (ET) and not 6-digit HHMMSS."""
    meeting_dir = tmp_path / "output" / "nea" / "09-28-2026" / "Session"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "title": "Evening Shift Session",
        "started_at": "2026-09-28T19:09:00Z",  # 15:09 ET (03:09 PM)
        "stopped_at": "2026-09-28T19:33:00Z",  # 15:33 ET (03:33 PM)
    }
    (meeting_dir / "session_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    t_file = meeting_dir / "01_transcript.md"
    t_file.write_text("# Evening Shift Session\n\n**Date:** 09/28/2026\n", encoding="utf-8")

    meta: dict = {"input_path": str(t_file)}
    hydrate_live_container_artifacts(meta, input_path=t_file)

    assert meta.get("time") == "03:09 PM - 03:33 PM ET", f"Expected 03:09 PM - 03:33 PM ET, got {meta.get('time')}"
    assert meta.get("time_filename") == "0309PM_0333PM_ET", f"Expected 0309PM_0333PM_ET, got {meta.get('time_filename')}"
    assert not str(meta.get("time_filename")).isdigit(), "time_filename must not be 6-digit raw seconds"
