"""
tests/test_unified_pipeline_runner.py

Acceptance test suite for Wave 5: Staged Ingestion Pipeline Framework Consolidation.
Verifies declarative manifest contracts, parsing stage normalization,
Output 01-03 construction, deterministic QA integration, and fail-closed release gates.
"""

import json
from pathlib import Path

import pytest

from dual_pipeline.manifest import IngestionMeetingManifest
from dual_pipeline.pipeline_runner import TranscriptIngestionPipelineRunner


@pytest.fixture
def sample_raw_transcript() -> str:
    return """
Anece Elakkari started transcription
Alex Rivera  3:34
Welcome everyone to the Insights Hub review. We are looking at Pano AI camera feeds.
Marcus Vance  3:36
Confirmed, the DTN weather feed is integrated into Point Viewer as well.
Anece Elakkari stopped transcription
"""


@pytest.fixture
def sample_signal_projection() -> dict:
    return {
        "schema_version": "5.7",
        "meeting_id": "NEET_Insights_Hub_Tech_Services",
        "classification": "confidential",
        "audiences": ["partner_advisory", "apex_global"],
        "evidence_index": {
            "ev-01": {
                "speaker": "Alex Rivera",
                "source_classification": "confidential",
                "text": "Pano AI camera feeds provide situational awareness across transmission lines.",
                "locator": {"kind": "timestamp", "value": "00:03:34"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:pano_ai",
                    "type": "technical",
                    "name": "Pano AI",
                    "mutation": "CREATE",
                },
                {
                    "id": "system:point_viewer",
                    "type": "technical",
                    "name": "Point Viewer",
                    "mutation": "CREATE",
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-01",
                    "from_id": "system:pano_ai",
                    "to_id": "system:point_viewer",
                    "relationship": "INTEGRATES_WITH",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "action_updates": [
                {
                    "action_id": "act-01",
                    "owner_id": "person:aaron_adams",
                    "status": "proposed",
                    "due_date": "10/01/2026",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "risk_updates": [],
        },
        "evidence_only_statement_ids": [],
    }


def test_manifest_validation_and_path_generation(tmp_path: Path):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Deep Dive",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="04:37 PM",
        raw_text="Test transcript",
        output_root=tmp_path / "output",
        intake_dir=tmp_path / "intake",
    )
    manifest.validate()

    assert manifest.human_date == "09/11/2026"
    assert manifest.path_date == "09-11-2026"
    assert manifest.iso_date == "2026-09-11"
    assert manifest.time_str == "03:34 PM - 04:37 PM"
    assert "09-11-2026" in str(manifest.resolved_output_dir)
    assert manifest.f1_filename.startswith("01_NEET_Tech_Services_Deep_Dive_09-11-2026_")
    assert manifest.f1_filename.endswith("_hitl_resolved_transcript.md")
    assert manifest.f2_filename.endswith("_executive_recap.md")
    assert manifest.f3_filename.endswith("_canonical_signal_projection.json")


def test_manifest_invalid_inputs_fail():
    # Empty project name
    with pytest.raises(ValueError, match="project_name cannot be empty"):
        IngestionMeetingManifest(
            project_name="",
            meeting_title="Test",
            meeting_date="09/11/2026",
            start_time_et="01:00 PM",
            end_time_et="02:00 PM",
            raw_text="Content",
        ).validate()

    # Invalid date
    with pytest.raises(ValueError, match="Invalid date format"):
        IngestionMeetingManifest(
            project_name="apex_trans",
            meeting_title="Test",
            meeting_date="2026.09.11",
            start_time_et="01:00 PM",
            end_time_et="02:00 PM",
            raw_text="Content",
        ).validate()

    # Missing transcript text and path
    with pytest.raises(ValueError, match="Either source_transcript_path or raw_text must be provided"):
        IngestionMeetingManifest(
            project_name="apex_trans",
            meeting_title="Test",
            meeting_date="09/11/2026",
            start_time_et="01:00 PM",
            end_time_et="02:00 PM",
        ).validate()


def test_parser_stage_normalization(sample_raw_transcript: str):
    speaker_map = {"Alex Rivera": "Alex Rivera (Apex Global)", "Marcus Vance": "Marcus Vance (Apex Global)"}
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Test Meeting",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="04:37 PM",
        raw_text=sample_raw_transcript,
        speaker_map=speaker_map,
    )

    raw_text, turns = TranscriptIngestionPipelineRunner.parse_transcript(manifest)
    assert len(turns) == 2
    assert turns[0][0] == "Alex Rivera (Apex Global)"
    assert turns[0][1] == "00:03:34"
    assert "Pano AI camera feeds" in turns[0][2]
    assert turns[1][0] == "Marcus Vance (Apex Global)"
    assert turns[1][1] == "00:03:36"
    assert "DTN weather feed" in turns[1][2]


def test_build_output_01_structure(sample_raw_transcript: str):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="04:37 PM",
        raw_text=sample_raw_transcript,
        attendees=[{"name": "Alex Rivera", "company": "Apex Global Enterprises", "role": "Lead"}],
        ambiguity_rows=[{"id": "AMB-01", "description": "Speaker verification", "statements": "stmt-01", "status": "RESOLVED", "pathway": "Direct log"}],
    )

    _, turns = TranscriptIngestionPipelineRunner.parse_transcript(manifest)
    f1_text = TranscriptIngestionPipelineRunner.build_output_01(manifest, turns)

    assert "# 01: Forensic Attributed Transcript" in f1_text
    assert "**Date:** 09/11/2026" in f1_text
    assert "**Time**: 03:34 PM - 04:37 PM" in f1_text
    assert "Alex Rivera" in f1_text
    assert "(Apex Global Enterprises)" in f1_text
    assert "| `AMB-01` | Speaker verification | `stmt-01` | `RESOLVED` |" in f1_text
    assert "### [00:03:34]" in f1_text
    assert "**Alex Rivera:**" in f1_text


def test_build_output_02_structure():
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="04:37 PM",
        meeting_objective="Demonstrate Pano AI and Point Viewer integrations.",
        raw_text="Dummy text",
        attendees=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
    )

    f2_text = TranscriptIngestionPipelineRunner.build_output_02(manifest)
    assert "# Subject: Executive Briefing" in f2_text
    assert "**Date:** 09/11/2026" in f2_text
    assert "## Meeting Objective" in f2_text
    assert "## Key Discussion Points & Findings" in f2_text
    assert "Demonstrate Pano AI and Point Viewer integrations." in f2_text
    assert "## Next Steps & Action Items" in f2_text
    assert "## Immediate Delivery Scope & Release Boundaries" in f2_text


def test_build_output_03_metadata_injection(sample_signal_projection: dict):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="04:37 PM",
        meeting_archetype="TECHNICAL_DEMONSTRATION",
        classification="confidential",
        audiences=["partner_advisory", "apex_global"],
        raw_text="Dummy text",
        f3_data=sample_signal_projection,
    )

    f3_out = TranscriptIngestionPipelineRunner.build_output_03(manifest)
    assert f3_out["schema_version"] == "5.7"
    assert f3_out["classification"] == "confidential"
    assert f3_out["audiences"] == ["partner_advisory", "apex_global"]
    assert f3_out["meeting_id"] == manifest.resolved_base_name


def test_pipeline_runner_dry_run_qa_e2e(tmp_path: Path, sample_raw_transcript: str, sample_signal_projection: dict):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="03:36 PM",
        meeting_archetype="TECHNICAL_DEMONSTRATION",
        raw_text=sample_raw_transcript,
        f3_data=sample_signal_projection,
        output_root=tmp_path / "output",
        intake_dir=tmp_path / "intake",
        attendees=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        roster=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        expected_domain_terms=["Pano AI", "Point Viewer"],
    )

    runner = TranscriptIngestionPipelineRunner()
    result = runner.run(manifest, dry_run_qa=True)

    assert result["status"] == "QA_PASSED_DRY_RUN"
    assert result["qa_passed"] is True
    assert manifest.f1_path.is_file()
    assert manifest.f2_path.is_file()
    assert manifest.f3_path.is_file()
    assert manifest.qa_report_path.is_file()

    qa_json = json.loads(manifest.qa_report_path.read_text())
    assert qa_json["passed"] is True


def test_fail_closed_release_authorization(tmp_path: Path, sample_raw_transcript: str, sample_signal_projection: dict, monkeypatch):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="03:36 PM",
        raw_text=sample_raw_transcript,
        f3_data=sample_signal_projection,
        output_root=tmp_path / "output",
        intake_dir=tmp_path / "intake",
        attendees=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        roster=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        expected_domain_terms=["Pano AI", "Point Viewer"],
    )

    # Mock evaluate_judge_and_release to return FAIL
    def mock_judge_fail(*args, **kwargs):
        return (
            {"status": "EVALUATED"},
            {"final_release_verdict": "FAIL", "scores": {"output_3_canonical_signal_projection": 65}},
        )

    import dual_pipeline.pipeline_runner as pr_mod
    monkeypatch.setattr(pr_mod, "evaluate_judge_and_release", mock_judge_fail)

    runner = TranscriptIngestionPipelineRunner()
    with pytest.raises(RuntimeError, match="Release authorization rejected"):
        runner.run(manifest, dry_run_qa=False)


def test_package_intake_integration(tmp_path: Path, sample_raw_transcript: str, sample_signal_projection: dict, monkeypatch):
    manifest = IngestionMeetingManifest(
        project_name="apex_trans",
        meeting_title="Tech Services Review",
        meeting_date="09/11/2026",
        start_time_et="03:34 PM",
        end_time_et="03:36 PM",
        raw_text=sample_raw_transcript,
        f3_data=sample_signal_projection,
        output_root=tmp_path / "output",
        intake_dir=tmp_path / "intake",
        attendees=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        roster=[{"name": "Alex Rivera", "company": "Apex Global Enterprises"}],
        expected_domain_terms=["Pano AI", "Point Viewer"],
    )

    # Mock judge to PASS and mock prepare_package to write intake file
    def mock_judge_pass(*args, **kwargs):
        return (
            {"status": "EVALUATED"},
            {"final_release_verdict": "PASS", "scores": {"output_3_canonical_signal_projection": 96}},
        )

    def mock_prepare_package(draft_path, source_export, source_location, output_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pkg_data = {"contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0", "status": "SEALED"}
        output_path.write_text(json.dumps(pkg_data))
        return pkg_data

    import dual_pipeline.pipeline_runner as pr_mod
    monkeypatch.setattr(pr_mod, "evaluate_judge_and_release", mock_judge_pass)
    monkeypatch.setattr(pr_mod, "prepare_package", mock_prepare_package)

    runner = TranscriptIngestionPipelineRunner()
    res = runner.run(manifest, dry_run_qa=False)
    assert res["status"] == "COMPLETE"
    assert res["release_verdict"] == "PASS"
    assert res["output_3_score"] == 96
    assert Path(res["pkg_path"]).is_file()
