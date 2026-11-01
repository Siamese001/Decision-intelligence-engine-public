"""End-to-end verification of Wave 4 Track 5 document ingestion, extractors, and statement retention."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from dual_pipeline.docx_source import extract_docx_core_metadata, extract_docx_markdown
from dual_pipeline.html_source import extract_html_markdown
from dual_pipeline.intake_core.change_set_helpers import should_suppress_document_candidate
from dual_pipeline.pre_intake_classifier import propose_pre_intake_classification
from tools.prepare_evidence_envelope_intake import _validate_semantic_yield


def _build_synthetic_docx_bytes(with_confidential_body: bool = True) -> bytes:
    """Builds a valid in-memory DOCX package with core.xml metadata and merged table cells."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        core_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/">'
            "<dc:title>Cloud Platform Architecture Spec</dc:title>"
            "<dc:creator>Speaker_Alpha Caldwell</dc:creator>"
            "<dcterms:created>2026-10-20T12:00:00Z</dcterms:created>"
            "</cp:coreProperties>"
        )
        zf.writestr("docProps/core.xml", core_xml.encode("utf-8"))
        marker = "Proprietary and Confidential - Highly Restricted Strategy" if with_confidential_body else "General Overview"
        doc_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>Cloud Platform Architecture Spec</w:t></w:r></w:p>"
            f"<w:p><w:r><w:t>{marker}</w:t></w:r></w:p>"
            "<w:tbl>"
            "<w:tr>"
            "<w:tc><w:p><w:r><w:t>Domain</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Target</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            '<w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Lakehouse</w:t></w:r></w:p></w:tc>'
            "<w:tc><w:p><w:r><w:t>Amit</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Q4</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
            "<w:tr>"
            "<w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>"
            '<w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Speaker_Beta Combined Scope</w:t></w:r></w:p></w:tc>'
            "</w:tr>"
            "</w:tbl>"
            "</w:body></w:document>"
        )
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
    return buf.getvalue()


def test_docx_core_metadata_and_merged_table_extraction_e2e() -> None:
    """Verifies DOCX core.xml metadata extraction and w:vMerge/w:gridSpan table alignment."""
    docx_bytes = _build_synthetic_docx_bytes()
    meta = extract_docx_core_metadata(docx_bytes)
    assert meta["title"] == "Cloud Platform Architecture Spec"
    assert meta["creator"] == "Speaker_Alpha Caldwell"
    assert meta["created"] == "2026-10-20T12:00:00Z"

    md = extract_docx_markdown(docx_bytes)
    assert "| Domain | Owner | Target |" in md
    assert "| --- | --- | --- |" in md
    # Row 2 carries down 'Lakehouse' from vMerge restart and pads gridSpan=2
    assert "| Lakehouse | Speaker_Beta Combined Scope |  |" in md


def test_html_table_separator_and_svg_text_extraction_e2e() -> None:
    """Verifies HTML table emits Markdown header separator and extracts visible <svg><text>."""
    html_bytes = (
        "<html><head><title>Architecture Diagram</title></head><body>"
        "<h2>Component Matrix</h2>"
        "<table><tr><th>Service</th><th>Latency</th></tr>"
        "<tr><td>Gateway</td><td>12ms</td></tr></table>"
        "<svg><text>Vector Architecture Node A</text></svg>"
        "</body></html>"
    ).encode("utf-8")
    md = extract_html_markdown(html_bytes)
    assert "| Service | Latency |" in md
    assert "| --- | --- |" in md
    assert "| Gateway | 12ms |" in md
    assert "Vector Architecture Node A" in md


def test_table_heavy_document_passes_semantic_yield_and_retains_entity_statements(tmp_path: Path) -> None:
    """Verifies table-heavy documents pass _validate_semantic_yield and entity-linked statements are not suppressed."""
    doc_path = tmp_path / "matrix_doc.md"
    table_rows = "\n".join(
        f"| Workstream {i} | Owner Person {i} | Deliverable Architecture Milestone {i} for Platform Modernization |"
        for i in range(1, 12)
    )
    doc_path.write_text(
        "# Architecture Deliverable Matrix\n\n"
        "| Workstream | Owner | Deliverable |\n"
        "| --- | --- | --- |\n"
        f"{table_rows}\n",
        encoding="utf-8",
    )
    # Previously raised ZeroSignalError because all '|' lines were stripped
    _validate_semantic_yield(doc_path, min_words=50)

    cand = {
        "candidate_id": "cand-doc-01",
        "candidate_type": "assertion",
        "entity_mentions": [
            {"reported_text": "Databricks Lakehouse", "stable_identifier": "system:databricks_lakehouse"},
        ],
    }
    assert (
        should_suppress_document_candidate(
            cand,
            selected_entities=["system:databricks_lakehouse"],
            doc_connected_entities={"system:databricks_lakehouse"},
            correction=None,
            a2a_source=False,
        )
        is False
    )
