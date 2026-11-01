"""
Unit and integration tests for Processed Copilot Notes Ingestion (Outputs 02 and 03).
Covers:
- Wave 1: Detection of processed Copilot notes and metadata scaffolding
- Wave 2: Direct Stage 02/03 routing and source notes preservation
- Wave 3: QA Gates & LLM Judge notes mode integration
- Wave 4: Output hierarchy governance & intake package sealing
- Wave 5: Full pipeline end-to-end execution
"""

from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
from dual_pipeline.transcript_pipeline_core.normalizer import (
    is_copilot_notes,
    normalize_raw_input_text,
)

USER_COPILOT_SAMPLE = """Clarifyng on-site expectations for Minneapolis consultants at Apex Global
9.16.2026
2-2:30 PM ET

Linda Dick
Barry Kern
Guillermo Chapellin
Amit Ayer

#### **Amit**
- **Client Strategy Behind On-Site Expectations**: Amit explained that the request for consultants to be on-site three days per week is tied to a broader effort to change the client relationship from a traditional staff augmentation model toward a more value-oriented partnership. He described staff augmentation as a model where resources are assigned to the client with limited emphasis on deliverables or consulting-led governance. According to Amit, leadership has been working to demonstrate additional value through professional services capabilities, intellectual capital, governance, and stronger client engagement. As part of that effort, leadership discussions with client executives established an expectation that consultants would be more visible on-site.

#### **Linda**
- **Travel Logistics & Flexibility**: Linda noted that travel arrangements for Minneapolis resources need clear cost-center attribution and that hybrid flexibility is essential during initial transition weeks.
"""

RAW_TRANSCRIPT_SAMPLE = """### [00:00:15]
**Amit Ayer:** Hello everyone, thanks for joining. Let's discuss the on-site expectations for Minneapolis consultants.

### [00:00:45]
**Linda Dick:** Thanks Amit. Travel logistics need clear cost-center attribution.
"""

WEBVTT_SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:04.000
<v Amit Ayer>Hello everyone, thanks for joining.</v>

00:00:05.000 --> 00:00:08.000
<v Linda Dick>Thanks Amit.</v>
"""

TEAMS_DOCX_SAMPLE = """Amit Ayer 10:15 AM
Hello everyone, thanks for joining.
Linda Dick 10:16 AM
Thanks Amit.
"""


# ============================================================================
# Wave 1: Copilot Notes Detection & Metadata Scaffolding Tests
# ============================================================================


def test_detection_user_sample():
    """Verify that the user's exact Copilot note structure is detected as Copilot notes."""
    assert is_copilot_notes(USER_COPILOT_SAMPLE) is True


def test_detection_various_notes_formats():
    """Verify detection on various structural notes patterns."""
    # Pattern with ### **Speaker**
    h3_sample = """### **Barry Kern**\n- **Contract Terms**: Barry confirmed SOW amendment details.\n"""
    assert is_copilot_notes(h3_sample) is True

    # Pattern with topic bullets without explicit speaker headers
    topic_bullets_sample = """## Discussion Summary\n- **Project Charter**: Completed phase 1.\n- **Timeline**: 4 weeks sprint.\n- **Signoff**: Pending VP approval.\n"""
    assert is_copilot_notes(topic_bullets_sample) is True

    # Pattern with speaker block and bullet points
    speaker_bullets_sample = """#### **Guillermo Chapellin**\n- Discussed system architecture dependencies.\n- Confirmed pipeline migration deadlines.\n"""
    assert is_copilot_notes(speaker_bullets_sample) is True


def test_detection_rejects_raw_transcripts():
    """Verify that raw timestamped transcripts are never detected as Copilot notes."""
    assert is_copilot_notes(RAW_TRANSCRIPT_SAMPLE) is False
    assert is_copilot_notes(WEBVTT_SAMPLE) is False
    assert is_copilot_notes(TEAMS_DOCX_SAMPLE) is False
    assert is_copilot_notes("") is False
    assert is_copilot_notes("Just a single line of text with no structure.") is False


def test_normalizer_preserves_notes_without_inventing_timestamps():
    """Verify that normalize_raw_input_text does not synthesize fake ### [00:00:00] turns on Copilot notes."""
    normalized = normalize_raw_input_text(USER_COPILOT_SAMPLE)
    # Must NOT invent pseudo-transcript timestamps
    assert "### [" not in normalized
    assert "00:00:00" not in normalized
    # Must preserve speaker headers and topic bullets
    assert "#### **Amit**" in normalized
    assert "- **Client Strategy Behind On-Site Expectations**:" in normalized


def test_scaffold_metadata_notes_mode(tmp_path: Path):
    """Verify that scaffold_meeting_metadata automatically configures notes mode for Copilot notes."""
    notes_file = tmp_path / "copilot_notes.md"
    notes_file.write_text(USER_COPILOT_SAMPLE, encoding="utf-8")

    meta = scaffold_meeting_metadata(
        input_path=notes_file,
        project="project_nexus",
    )

    assert meta.get("notes_mode") is True
    assert meta.get("is_notes") is True
    assert meta.get("source_type") == "copilot_notes"
    assert meta.get("skip_output_1") is True
    assert meta.get("outputs") == ["02", "03"]
    assert meta.get("date") == "09/16/2026"
    assert meta.get("date_filename") == "09-16-2026"
    assert "2-2:30 PM ET" in meta.get("time", "")

    # Check extracted speaker attendees
    attendees = meta.get("attendees", {})
    if isinstance(attendees, dict):
        all_att = [person for sub in attendees.values() for person in sub]
    else:
        all_att = attendees
    assert any("Amit" in att for att in all_att)


# ============================================================================
# Wave 2 & 3: QA Gates, Micro-Judge & Pipeline Execution Tests
# ============================================================================

def test_qa_and_judge_notes_mode():
    """Verify that Gate 1 and Output 1 Judge are skipped with PASS status in notes mode."""
    from dual_pipeline.transcript_qa import evaluate_all_qa_gates, evaluate_judge_and_release
    from dual_pipeline.transcript_stages_core.synthesizers import OfflineTranscriptSynthesizer

    meta = {
        "notes_mode": True,
        "is_notes": True,
        "outputs": ["02", "03"],
        "date": "09/16/2026",
        "meeting_name": "Clarifyng on-site expectations",
        "roster": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            {"name": "Linda Dick", "org": "Apex Global Enterprises"},
        ],
        "attendees": ["Amit Ayer", "Linda Dick"],
    }

    synth = OfflineTranscriptSynthesizer()
    valid_recap = synth.synthesize_recap("", user_content_or_metadata=meta)
    valid_graph = synth.synthesize_graph("", user_content_or_metadata=meta)

    # Evaluate QA gates
    qa_report = evaluate_all_qa_gates(
        f1_text="",
        f2_text=valid_recap,
        f3_text=valid_graph,
        source_text=USER_COPILOT_SAMPLE,
        metadata=meta,
    )

    assert qa_report["passed"] is True
    assert qa_report["output_01_forensic_transcript"]["status"] == "SKIPPED"
    assert qa_report["output_01_forensic_transcript"]["reason"] == "NOT_APPLICABLE_NOTES_MODE"
    assert qa_report["output_02_executive_recap"]["passed"] is True
    assert qa_report["output_03_canonical_signal_projection"]["passed"] is True
    assert qa_report["cross_artifact_consistency"]["passed"] is True

    # Evaluate Judge and release
    qa_res, judge_res, final_decision = evaluate_judge_and_release(
        f1_text="",
        f2_text=valid_recap,
        f3_text=valid_graph,
        source_text=USER_COPILOT_SAMPLE,
        metadata=meta,
        mock=True,
    )

    assert final_decision["status"] == "PASS"
    assert final_decision["authoritative_write_authorized"] is True
    assert final_decision["output_1_pass"] is True
    assert final_decision["output_2_pass"] is True
    assert final_decision["output_3_pass"] is True
    assert judge_res["output_1_evaluation"]["status"] == "SKIPPED"
    assert judge_res["output_1_evaluation"]["composite_score"] == 100


def test_pipeline_execution_notes_mode(tmp_path: Path):
    """Verify that RawTranscriptPipeline with notes_mode=True produces only Deliverables 02 & 03 and receipts."""
    from dual_pipeline.transcript_pipeline import RawTranscriptPipeline

    notes_file = tmp_path / "copilot_notes.md"
    notes_file.write_text(USER_COPILOT_SAMPLE, encoding="utf-8")

    meta = scaffold_meeting_metadata(
        input_path=notes_file,
        project="project_nexus",
    )

    out_dir = tmp_path / "output_test_run"
    pipeline = RawTranscriptPipeline(provider_type="offline")
    result = pipeline.run(
        str(notes_file),
        metadata=meta,
        output_dir=str(out_dir),
    )

    # Deliverable 01 must NOT be generated
    assert result["01_transcript"] == ""
    assert not (out_dir / "01_Clarifyng_on-site_expectations_for_Minneapolis_consultants_at_Apex Global_09-16-2026_02-00-PM-02-30-PM-ET_hitl_resolved_transcript.md").exists()
    assert len(list(out_dir.glob("01_*"))) == 0

    # Deliverable 02 and 03 must exist
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()

    # Source notes must be preserved on disk
    assert result["source_notes"] != ""
    source_notes_path = Path(result["source_notes"])
    assert source_notes_path.exists()
    assert "Clarifyng on-site expectations" in source_notes_path.read_text(encoding="utf-8")

    # All 4 governance receipts must exist
    assert (out_dir / "qa_gates_report.json").exists()
    assert (out_dir / "llm_judge_evaluation.json").exists()
    assert (out_dir / "final_release_decision.json").exists()
    assert (out_dir / "intake_package.json").exists()

    # metadata.json must exist with notes_mode
    meta_json_file = out_dir / "metadata.json"
    assert meta_json_file.exists()
    mdata = json.loads(meta_json_file.read_text(encoding="utf-8"))
    assert mdata.get("notes_mode") is True
    assert mdata.get("outputs") == ["02", "03"]


def test_notes_pipeline_facade(tmp_path: Path):
    """Verify that NotesPipeline facade automatically enables notes_mode."""
    from dual_pipeline.notes_pipeline import NotesPipeline

    notes_file = tmp_path / "copilot_notes.md"
    notes_file.write_text(USER_COPILOT_SAMPLE, encoding="utf-8")

    meta = {
        "project": "project_nexus",
        "meeting_name": "Facade_Notes_Test",
        "date": "09/16/2026",
        "time": "2-2:30 PM ET",
        "roster": ["Amit Ayer", "Linda Dick", "Barry Kern", "Guillermo Chapellin"],
    }

    out_dir = tmp_path / "output_facade_run"
    pipeline = NotesPipeline(provider_type="offline")
    result = pipeline.run(
        str(notes_file),
        metadata=meta,
        output_dir=str(out_dir),
    )

    assert result["01_transcript"] == ""
    assert len(list(out_dir.glob("01_*"))) == 0
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()
    assert Path(result["source_notes"]).exists()


# ============================================================================
# Wave 4 & 5: Hierarchy Validation, Intake Package Sealing & CLI Tests
# ============================================================================


def test_hierarchy_and_intake_validation(tmp_path: Path):
    """Verify that validate_output_hierarchy certifies 02 & 03 notes container and seals intake package against source notes."""
    from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
    from tools.validate_output_hierarchy import validate_meeting_directory

    # Setup container inside governed hierarchy structure: <tmp>/output/<project>/<date>/<slug>/
    output_root = tmp_path / "output"
    project_dir = output_root / "project_nexus"
    date_dir = project_dir / "09-16-2026"
    slug_dir = date_dir / "Consultants_OnSite_Expectations"
    slug_dir.mkdir(parents=True, exist_ok=True)

    notes_file = tmp_path / "copilot_notes.md"
    notes_file.write_text(USER_COPILOT_SAMPLE, encoding="utf-8")

    meta = {
        "project": "project_nexus",
        "meeting_name": "Consultants_OnSite_Expectations",
        "date": "09/16/2026",
        "date_filename": "09-16-2026",
        "time": "2-2:30 PM ET",
        "time_filename": "02-00-PM-02-30-PM-ET",
        "standardized_slug": "Consultants_OnSite_Expectations",
        "roster": ["Amit Ayer", "Linda Dick", "Barry Kern", "Guillermo Chapellin"],
        "notes_mode": True,
        "outputs": ["02", "03"],
    }

    pipeline = RawTranscriptPipeline(provider_type="offline")
    pipeline.run(
        str(notes_file),
        metadata=meta,
        output_dir=str(slug_dir),
    )

    # Validate output hierarchy
    violations = validate_meeting_directory(date_dir, project_name="project_nexus")
    assert violations == [], f"Unexpected hierarchy violations: {violations}"

    # Verify intake package is sealed against the preserved source notes
    pkg_path = slug_dir / "intake_package.json"
    assert pkg_path.exists()
    pkg_data = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert "meeting" in pkg_data
    assert "source_location" in pkg_data["meeting"]
    assert pkg_data["meeting"]["source_location"].endswith("_source.txt")


def test_cli_ingest_meeting_notes_mode(tmp_path: Path):
    """Verify that CLI nee ingest-meeting --notes executes and produces Deliverables 02 & 03."""
    import dual_pipeline.cli

    notes_file = tmp_path / "copilot_notes.md"
    notes_file.write_text(USER_COPILOT_SAMPLE, encoding="utf-8")
    out_dir = tmp_path / "cli_notes_out"

    exit_code = dual_pipeline.cli.main([
        "ingest-meeting",
        "--input", str(notes_file),
        "--notes",
        "--project", "project_nexus",
        "--meeting", "CLI_Notes_Execution",
        "--date", "09/16/2026",
        "--time", "2-2:30 PM ET",
        "--output-dir", str(out_dir),
        "--provider", "offline",
    ])

    assert exit_code == 0
    assert len(list(out_dir.glob("01_*"))) == 0
    assert len(list(out_dir.glob("02_*"))) == 1
    assert len(list(out_dir.glob("03_*"))) == 1
    assert len(list(out_dir.glob("*_source.txt"))) == 1
    assert (out_dir / "intake_package.json").exists()

