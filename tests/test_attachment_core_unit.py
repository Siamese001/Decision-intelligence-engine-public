"""
Unit tests for dual_pipeline.attachment_core subsystem.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.attachment_core import (
    AttachmentType,
    align_attachments_with_transcript,
    build_attachment_prompt_section,
    classify_attachment_type,
    discover_attachments,
    extract_attachment_content,
    stage_attachments_into_output,
)


def test_classify_attachment_type():
    """Verify extension to AttachmentType classification."""
    assert classify_attachment_type("diagram.png") == AttachmentType.IMAGE
    assert classify_attachment_type("photo.jpeg") == AttachmentType.IMAGE
    assert classify_attachment_type("schema.svg") == AttachmentType.DIAGRAM
    assert classify_attachment_type("flow.drawio") == AttachmentType.DIAGRAM
    assert classify_attachment_type("deck.pptx") == AttachmentType.PRESENTATION
    assert classify_attachment_type("memo.docx") == AttachmentType.DOCUMENT
    assert classify_attachment_type("doc.pdf") == AttachmentType.DOCUMENT
    assert classify_attachment_type("rates.csv") == AttachmentType.SPREADSHEET
    assert classify_attachment_type("unknown.xyz") == AttachmentType.UNKNOWN


def test_discover_and_stage_attachments():
    """Verify discovery from explicit paths and staging into referenced_artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Create dummy attachments
        img1 = tmp_path / "system_architecture.png"
        img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy_png_bytes")

        doc1 = tmp_path / "integration_spec.md"
        doc1.write_text("# Integration Spec\n\n- API Gateway connects to Auth Service.", encoding="utf-8")

        out_dir = tmp_path / "output_meeting"
        out_dir.mkdir()

        discovered = discover_attachments([img1, doc1], meeting_output_dir=out_dir)
        assert len(discovered) == 2
        assert img1.resolve() in discovered
        assert doc1.resolve() in discovered

        # Stage into output
        records = stage_attachments_into_output(discovered, out_dir)
        assert len(records) == 2

        staged_files = list((out_dir / "referenced_artifacts").iterdir())
        assert len(staged_files) == 2

        rec_map = {r.filename: r for r in records}
        assert "system_architecture.png" in rec_map
        assert rec_map["system_architecture.png"].attachment_type == AttachmentType.IMAGE
        assert rec_map["system_architecture.png"].relative_staged_path == "referenced_artifacts/system_architecture.png"
        assert len(rec_map["system_architecture.png"].sha256) == 64


def test_svg_diagram_extraction():
    """Verify component and connection extraction from SVG diagram XML."""
    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200">'
        b'<text x="50" y="50">API Gateway</text>'
        b'<line x1="50" y1="60" x2="150" y2="60" />'
        b'<text x="150" y="50">Auth Service</text>'
        b'</svg>'
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        svg_file = tmp_path / "architecture_flow.svg"
        svg_file.write_bytes(svg_content)

        out_dir = tmp_path / "output"
        records = stage_attachments_into_output([svg_file], out_dir)
        assert len(records) == 1

        extracted = extract_attachment_content(records[0], mock_mode=True)
        assert extracted.has_diagrams is True
        assert len(extracted.components) >= 2
        labels = [c["label"] for c in extracted.components]
        assert "API Gateway" in labels
        assert "Auth Service" in labels
        assert len(extracted.connections) >= 1


def test_cross_modal_alignment_and_prompt_builder():
    """Verify turn-to-attachment alignment and Stage L2 prompt building."""
    transcript = (
        "## Transcript\n\n"
        "**Amit Ayer:** [00:01:05] If everyone looks at the architecture diagram on the screen, "
        "you can see the API Gateway routing.\n\n"
        "**Andy Kilinskis:** [00:02:30] Yes, and in the system architecture diagram we also have the auth flow.\n"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        img_file = tmp_path / "system_architecture_diagram.svg"
        img_file.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><text>system architecture diagram</text></svg>')

        out_dir = tmp_path / "out"
        records = stage_attachments_into_output([img_file], out_dir)

        contents = {r.attachment_id: extract_attachment_content(r, mock_mode=True) for r in records}
        alignment = align_attachments_with_transcript(transcript, records, contents)

        assert len(alignment.mentions) >= 1
        assert alignment.total_attachments == 1

        prompt_block = build_attachment_prompt_section(records, contents, alignment)
        assert "## Uploaded Meeting Collateral & Visual Evidence" in prompt_block
        assert "system_architecture_diagram.svg" in prompt_block
        assert "referenced_artifacts/system_architecture_diagram.svg" in prompt_block
        assert "Correlated Spoken Dialogue Turns" in prompt_block
