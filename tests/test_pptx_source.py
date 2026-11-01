"""
tests/test_pptx_source.py — Unit tests for pure-stdlib PPTX extraction engine.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from dual_pipeline.pptx_source import (
    PPTXPresentation,
    extract_pptx_markdown,
    extract_pptx_presentation,
)


def _create_synthetic_pptx_bytes() -> bytes:
    """Build a valid minimal in-memory OpenXML PowerPoint (.pptx) file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # [Content_Types].xml
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/notesSlides/notesSlide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>
</Types>""",
        )

        # _rels/.rels
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>""",
        )

        # ppt/presentation.xml
        zf.writestr(
            "ppt/presentation.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldIdLst>
    <p:sldId id="256" r:id="rId1"/>
    <p:sldId id="257" r:id="rId2"/>
  </p:sldIdLst>
</p:presentation>""",
        )

        # ppt/_rels/presentation.xml.rels
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
</Relationships>""",
        )

        # ppt/slides/slide1.xml
        zf.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title 1"/><p:nvPr><p:ph type="ctrTitle"/></p:nvPr></p:nvSpPr>
        <p:txBody>
          <a:p><a:r><a:t>Synthetic Strategy Deck</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Subtitle 2"/><p:nvPr><p:ph type="subTitle"/></p:nvPr></p:nvSpPr>
        <p:txBody>
          <a:p><a:r><a:t>High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. </a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:sld>""",
        )

        # ppt/slides/slide2.xml (with table and bullets)
        zf.writestr(
            "ppt/slides/slide2.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Title 1"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:txBody>
          <a:p><a:r><a:t>Market Operations</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Content 2"/></p:nvSpPr>
        <p:txBody>
          <a:p><a:r><a:t>Core Objective 1</a:t></a:r></a:p>
          <a:p><a:pPr lvl="1"/><a:r><a:t>Sub Point Alpha</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
      <p:graphicFrame>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">
            <a:tbl>
              <a:tr>
                <a:tc><a:txBody><a:p><a:r><a:t>Quarter</a:t></a:r></a:p></a:txBody></a:tc>
                <a:tc><a:txBody><a:p><a:r><a:t>Target Revenue</a:t></a:r></a:p></a:txBody></a:tc>
              </a:tr>
              <a:tr>
                <a:tc><a:txBody><a:p><a:r><a:t>Q1</a:t></a:r></a:p></a:txBody></a:tc>
                <a:tc><a:txBody><a:p><a:r><a:t>$21M</a:t></a:r></a:p></a:txBody></a:tc>
              </a:tr>
            </a:tbl>
          </a:graphicData>
        </a:graphic>
      </p:graphicFrame>
    </p:spTree>
  </p:cSld>
</p:sld>""",
        )

        # ppt/slides/_rels/slide2.xml.rels
        zf.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide2.xml"/>
</Relationships>""",
        )

        # ppt/notesSlides/notesSlide2.xml
        zf.writestr(
            "ppt/notesSlides/notesSlide2.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="1" name="Notes 1"/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
        <p:txBody>
          <a:p><a:r><a:t>Emphasize quarterly growth trajectory.</a:t></a:r></a:p>
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:notes>""",
        )

    return buf.getvalue()


def test_synthetic_pptx_extraction():
    """Verify that in-memory synthetic PPTX extracts accurately with title, bullets, tables, and notes."""
    raw_bytes = _create_synthetic_pptx_bytes()
    pres = extract_pptx_presentation(raw_bytes)

    assert isinstance(pres, PPTXPresentation)
    assert pres.slide_count == 2
    assert pres.title == "Synthetic Strategy Deck"
    assert len(pres.slides) == 2

    # Slide 1 verification
    s1 = pres.slides[0]
    assert s1.slide_number == 1
    assert s1.title == "Synthetic Strategy Deck"
    assert "High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. High Signal Testing internal. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing." in s1.to_markdown()

    # Slide 2 verification (bullets, tables, notes)
    s2 = pres.slides[1]
    assert s2.slide_number == 2
    assert s2.title == "Market Operations"
    s2_md = s2.to_markdown()
    assert "Core Objective 1" in s2_md
    assert "- Sub Point Alpha" in s2_md
    assert "| Quarter | Target Revenue |" in s2_md
    assert "| Q1 | $21M |" in s2_md
    assert "**Presenter Notes:**" in s2_md
    assert "Emphasize quarterly growth trajectory." in s2_md

    # Global extracted markdown & digest
    assert len(pres.extracted_markdown_sha256) == 64
    assert "# Synthetic Strategy Deck" in pres.extracted_markdown


def test_extract_pptx_markdown_shortcut():
    """Verify extract_pptx_markdown helper returns clean markdown string."""
    raw_bytes = _create_synthetic_pptx_bytes()
    md = extract_pptx_markdown(raw_bytes)
    assert md.startswith("# Synthetic Strategy Deck")
    assert "## Slide 2: Market Operations" in md


def test_malformed_zip_rejection():
    """Non-zip or corrupted bytes must raise ValueError."""
    corrupted_bytes = b"NOT_A_VALID_ZIP_HEADER_12345"
    with pytest.raises(ValueError, match="not a valid PPTX zip package"):
        extract_pptx_presentation(corrupted_bytes)


def test_empty_bytes_rejection():
    """Empty payload must raise ValueError."""
    with pytest.raises(ValueError):
        extract_pptx_presentation(b"")


def test_payload_exceeding_byte_limit(monkeypatch):
    """Payloads with slide XML exceeding _MAX_SLIDE_XML_BYTES must be blocked."""
    import dual_pipeline.pptx_source as pptx_module
    monkeypatch.setattr(pptx_module, "_MAX_SLIDE_XML_BYTES", 50)
    raw_bytes = _create_synthetic_pptx_bytes()
    with pytest.raises(ValueError, match="exceeds maximum byte ceiling"):
        extract_pptx_presentation(raw_bytes)


def test_real_florida_playbook_if_present():
    """If the Florida Playbook is on local filesystem, verify end-to-end extraction."""
    local_path = Path("/Users/amitayer/Library/CloudStorage/OneDrive-PartnerAdvisory/Documents/AI-GTM/Florida 2027 Account Planning Playbook.pptx")
    if not local_path.exists():
        pytest.skip("Florida Playbook PPTX not present in local OneDrive path.")

    pres = extract_pptx_presentation(local_path)
    assert pres.slide_count == 73
    assert "Florida" in pres.title or pres.slide_count > 50
    # Check that slide 6 or 35 markdown includes table markers
    has_table = any("|" in s.to_markdown() for s in pres.slides)
    assert has_table, "Expected to extract tables from Florida Playbook"
