"""
Tests verifying that large ASCII architecture diagrams up to 12,000 characters
are preserved without truncation in prompt builders and that ASCII topologies
are correctly identified as diagrams.
"""
import tempfile
from pathlib import Path

from dual_pipeline.attachment_core.extractors import extract_attachment_content
from dual_pipeline.attachment_core.prompt_builder import (
    _format_content_details,
    build_attachment_prompt_section,
)
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)

SAMPLE_ASCII_DIAGRAM = """
┌─────────────────────────────────────────────────────────────────────────────┐
│              LENS Autonomous Asset Intelligence Pipeline (v1.2)             │
└─────────────────────────────────────────────────────────────────────────────┘
  │
  ├── [STAGE 1: Field Capture & Edge Pre-Flight]
  │     │
  │     ├── Technicians capture asset photos via Mobile App
  │     └── Edge validation validates focus, glare, and resolution
  │
  ├── [STAGE 2: Multi-Model Extraction Engine]
  │     │
  │     ├── Primary: GPT-4o Vision extracts nameplate OCR and attributes
  │     └── Secondary: Claude 3.5 Sonnet performs cross-validation
  │
  └── [STAGE 3: Enterprise Integration & Settlement]
        │
        └── Verified data writes to SAP / Maximo Asset Registry
""" * 5  # ~3,500 characters


def test_ascii_diagram_preserved_without_truncation():
    assert len(SAMPLE_ASCII_DIAGRAM) > 2000

    content = ExtractedAttachmentContent(
        attachment_id="att-lens-ascii-01",
        filename="LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2.txt",
        attachment_type=AttachmentType.DIAGRAM,
        sha256="abc123def456",
        title="LENS Pipeline v1.2",
        extracted_text=SAMPLE_ASCII_DIAGRAM,
        summary="LENS Autonomous Pipeline Diagram",
        has_diagrams=True,
    )

    lines = _format_content_details(content)
    joined = "\n".join(lines)

    assert "... [truncated]" not in joined
    assert "LENS Autonomous Asset Intelligence Pipeline (v1.2)" in joined
    assert "Enterprise Integration & Settlement" in joined


def test_prompt_builder_includes_collateral_grounding_instructions():
    """Verifies prompt builder emits GROUNDING RULE and thumbnail embed instructions for Output 01/02."""
    att = AttachmentRecord(
        attachment_id="att-lens-ascii-01",
        original_path=Path("/tmp/LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2.txt"),
        staged_path=Path("/tmp/staged_lens.txt"),
        attachment_type=AttachmentType.DIAGRAM,
        filename="LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2.txt",
        file_size_bytes=len(SAMPLE_ASCII_DIAGRAM),
        sha256="abc123def456",
        relative_staged_path="referenced_artifacts/LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2.txt",
    )
    content = ExtractedAttachmentContent(
        attachment_id="att-lens-ascii-01",
        filename="LENS_Autonomous_Asset_Intelligence_Pipeline_v1.2.txt",
        attachment_type=AttachmentType.DIAGRAM,
        sha256="abc123def456",
        title="LENS Pipeline v1.2",
        extracted_text=SAMPLE_ASCII_DIAGRAM,
        summary="LENS Autonomous Pipeline Diagram",
        has_diagrams=True,
    )

    prompt_section = build_attachment_prompt_section(
        attachments=[att],
        extracted_contents={att.attachment_id: content},
    )

    assert "## Uploaded Meeting Collateral & Visual Evidence" in prompt_section
    # New: GROUNDING RULE block replaces old ASCII-only instruction
    assert "GROUNDING RULE" in prompt_section
    assert "COLLATERAL_REVIEWED" in prompt_section
    assert "LENS Autonomous Asset Intelligence Pipeline (v1.2)" in prompt_section
    assert "... [truncated]" not in prompt_section


def test_extract_attachment_content_detects_ascii_box_drawing():
    with tempfile.TemporaryDirectory() as tmpdir:
        diagram_file = Path(tmpdir) / "pipeline_diagram.txt"
        diagram_file.write_text(SAMPLE_ASCII_DIAGRAM, encoding="utf-8")

        record = AttachmentRecord(
            attachment_id="att-test-01",
            original_path=diagram_file,
            staged_path=diagram_file,
            attachment_type=AttachmentType.DOCUMENT,
            filename="pipeline_diagram.txt",
            file_size_bytes=diagram_file.stat().st_size,
            sha256="1234567890abcdef",
            relative_staged_path="referenced_artifacts/pipeline_diagram.txt",
        )

        extracted = extract_attachment_content(record)
        assert extracted.has_diagrams is True
        assert extracted.attachment_type == AttachmentType.DIAGRAM
        assert len(extracted.extracted_text) == len(SAMPLE_ASCII_DIAGRAM)


def test_content_exceeding_60k_ceiling_is_truncated():
    """Content exceeding 60,000 chars is truncated; 15k content is preserved intact."""
    # Below ceiling (15k): must NOT be truncated
    mid_text = "A" * 15000
    content_mid = ExtractedAttachmentContent(
        attachment_id="att-mid-01",
        filename="mid.txt",
        attachment_type=AttachmentType.DOCUMENT,
        sha256="abcdef",
        title="Mid Doc",
        extracted_text=mid_text,
    )
    lines_mid = _format_content_details(content_mid)
    joined_mid = "\n".join(lines_mid)
    assert "... [truncated]" not in joined_mid, "15k content must not be truncated (ceiling is 60k)"

    # Above ceiling (65k): must be truncated
    massive_text = "B" * 65000
    content_massive = ExtractedAttachmentContent(
        attachment_id="att-massive-01",
        filename="massive.txt",
        attachment_type=AttachmentType.DOCUMENT,
        sha256="abcdef2",
        title="Massive Doc",
        extracted_text=massive_text,
    )
    lines_massive = _format_content_details(content_massive)
    joined_massive = "\n".join(lines_massive)
    assert "... [truncated]" in joined_massive, "65k content must be truncated at 60k ceiling"
