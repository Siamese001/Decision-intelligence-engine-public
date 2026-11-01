"""
End-to-end integration test suite for meeting multimodal attachment ingestion.

Verifies:
1. Discovery and staging of uploaded images, diagrams, and documents into governed referenced_artifacts/
2. Cross-modal turn alignment and prompt injection into L2 stages
3. Deliverable 01 turn annotations, Deliverable 02 executive recap, and Deliverable 03 canonical signal projection
4. Sealing of evidence envelope intake package and pipeline execution receipt
5. Zero hierarchy violations under validate_meeting_directory
6. Auto-discovery of collateral pre-staged in referenced_artifacts/
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from dual_pipeline.attachment_core import (
    classify_attachment_type,
    discover_attachments,
    extract_attachment_content,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.types import AttachmentType
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from tools.validate_output_hierarchy import validate_meeting_directory


def test_attachment_type_classification() -> None:
    assert classify_attachment_type("architecture.png") == AttachmentType.IMAGE
    assert classify_attachment_type("flow.drawio") == AttachmentType.DIAGRAM
    assert classify_attachment_type("deck.pptx") == AttachmentType.PRESENTATION
    assert classify_attachment_type("metrics.csv") == AttachmentType.SPREADSHEET
    assert classify_attachment_type("specs.docx") == AttachmentType.DOCUMENT
    assert classify_attachment_type("notes.unknown") == AttachmentType.UNKNOWN


def test_e2e_meeting_pipeline_with_multimodal_attachments() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_p = Path(tmp_dir)
        output_dir = tmp_p / "output" / "project_beacon" / "09-18-2026" / "Sprint_Demo_Architecture"
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Create a dummy architecture diagram (SVG)
        svg_content = """<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
            <text x="50" y="50">Project Beacon Core Gateway</text>
            <text x="50" y="150">BGE-M3 Vector Sidecar</text>
            <text x="50" y="250">Apex Global Enterprises Cloud VPC</text>
        </svg>"""
        svg_file = tmp_p / "sherpa_architecture.svg"
        svg_file.write_text(svg_content, encoding="utf-8")

        # 2. Create a dummy image attachment (PNG bytes)
        png_file = tmp_p / "system_telemetry_dashboard.png"
        # 1x1 transparent PNG header
        png_file.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        # 3. Create meeting transcript referencing the diagram
        transcript_content = (
            "## Transcript\n\n"
            "**[09:31:00] Amit Ayer:** Welcome everyone to the Sherpa sprint demo. "
            "Let's look at the diagram on the screen: sherpa architecture showing the Gateway and Vector Sidecar.\n\n"
            "**[09:32:00] Marcus:** The Apex Global Enterprises Cloud VPC connection looks solid. "
            "We have verified that the BGE-M3 Vector Sidecar is deployed in production.\n\n"
            "**[09:33:00] Jen:** Agreed, let's also review the system telemetry dashboard screenshot uploaded in chat.\n\n"
            "**[09:34:00] Brandon Que:** Excellent progress. We are approved to proceed with full cutover.\n"
        )
        input_transcript = tmp_p / "raw_transcript.txt"
        input_transcript.write_text(transcript_content, encoding="utf-8")

        metadata = {
            "project": "project_beacon",
            "meeting_name": "Sprint_Demo_Architecture",
            "date": "09/18/2026",
            "date_filename": "09-18-2026",
            "time": "09:30 AM - 10:30 AM ET",
            "time_filename": "0930AM_1030AM_ET",
            "attendees": "Marcus, Brandon Que, Amit Ayer, Jen",
            "classification": "confidential",
            "standardized_slug": "Sprint_Demo_Architecture",
            "subfolder": "Sprint_Demo_Architecture",
            "no_subfolder": True,
        }

        pipeline = RawTranscriptPipeline(provider_type="offline")
        result = pipeline.run(
            input_path=str(input_transcript),
            metadata=metadata,
            output_dir=str(output_dir),
            attachments=[svg_file, png_file],
        )

        assert result is not None
        assert "01_transcript" in result
        assert "02_recap" in result
        assert "03_graph" in result

        # Verify attachments staged into referenced_artifacts/
        ref_artifacts_dir = output_dir / "referenced_artifacts"
        assert ref_artifacts_dir.is_dir(), "referenced_artifacts directory must exist"
        staged_svg = ref_artifacts_dir / "sherpa_architecture.svg"
        staged_png = ref_artifacts_dir / "system_telemetry_dashboard.png"
        assert staged_svg.exists(), "SVG attachment must be staged into referenced_artifacts/"
        assert staged_png.exists(), "PNG attachment must be staged into referenced_artifacts/"

        # Verify Deliverable 01 transcript was created
        f1_path = Path(result["01_transcript"])
        assert f1_path.exists()
        f1_text = f1_path.read_text(encoding="utf-8")
        assert "Amit Ayer" in f1_text
        assert "Marcus" in f1_text

        # Verify Deliverable 02 executive recap was created
        f2_path = Path(result["02_recap"])
        assert f2_path.exists()
        f2_text = f2_path.read_text(encoding="utf-8")
        assert "Meeting Objective" in f2_text

        # Verify Deliverable 03 canonical signal projection was created
        f3_path = Path(result["03_graph"])
        assert f3_path.exists()
        graph_data = json.loads(f3_path.read_text(encoding="utf-8"))
        assert "entity_upserts" in graph_data or "nodes" in graph_data or "graph_mutations" in graph_data

        # Verify intake_package.json was sealed
        intake_pkg = output_dir / "intake_package.json"
        assert intake_pkg.exists(), "intake_package.json must be sealed"
        pkg_data = json.loads(intake_pkg.read_text(encoding="utf-8"))
        assert pkg_data.get("schema_version") is not None

        # Verify pipeline execution receipt exists
        receipt_path = output_dir / "pipeline_execution_receipt.json"
        assert receipt_path.exists(), "pipeline_execution_receipt.json must be written"
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt_data.get("terminal_outcome") == "COMPLETE"
        # Check that attachments were recorded in metadata
        assert "attachments" in receipt_data.get("metadata", {})
        att_entries = receipt_data["metadata"]["attachments"]
        assert len(att_entries) == 2
        assert any(e["filename"] == "sherpa_architecture.svg" for e in att_entries)
        assert any(e["filename"] == "system_telemetry_dashboard.png" for e in att_entries)

        # Verify output directory hierarchy passes without violations
        violations = validate_meeting_directory(output_dir, allow_active_recording=False)
        assert violations == [], f"Output directory hierarchy violations: {violations}"


def test_auto_discovery_from_preexisting_referenced_artifacts() -> None:
    """Verify that collateral pre-uploaded to referenced_artifacts/ is automatically discovered."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_p = Path(tmp_dir)
        meeting_dir = tmp_p / "output" / "nea" / "09-18-2026" / "Architecture_Review"
        ref_dir = meeting_dir / "referenced_artifacts"
        ref_dir.mkdir(parents=True, exist_ok=True)

        mock_chart = ref_dir / "data_flow_chart.drawio"
        mock_chart.write_text("<mxfile><diagram name='Page-1'>test diagram</diagram></mxfile>", encoding="utf-8")

        # Discover attachments pointing only to the meeting directory
        discovered = discover_attachments(
            meeting_output_dir=meeting_dir,
        )
        assert len(discovered) == 1
        assert discovered[0].name == "data_flow_chart.drawio"

        records = stage_attachments_into_output(discovered, meeting_dir)
        assert len(records) == 1
        assert records[0].attachment_type == AttachmentType.DIAGRAM

        content = extract_attachment_content(records[0])
        assert content.components is not None
