"""
tests/end_to_end/test_parallel_document_ingestion_e2e.py

End-to-End Acceptance Test Suite for Wave 3:
Governed parallel multi-document ingestion across heterogeneous document formats (.docx, .xlsx, .pdf, .md)
with background BGE-M3 vector pre-computation, conflict-free delta rebasing, and group commits.
"""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import openpyxl
import pypdf

from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline_core.parallel_ingestor import (
    ParallelDocumentIngestor,
    ingest_documents_parallel,
)


def _build_docx_bytes(title: str, text: str) -> bytes:
    """Construct a clean, valid DOCX byte stream."""
    _W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    document = ET.Element(f"{_W}document")
    body = ET.SubElement(document, f"{_W}body")

    def paragraph(parent: ET.Element, content: str, heading: bool = False) -> None:
        p = ET.SubElement(parent, f"{_W}p")
        if heading:
            pPr = ET.SubElement(p, f"{_W}pPr")
            ET.SubElement(pPr, f"{_W}pStyle", {f"{_W}val": "Heading1"})
        r = ET.SubElement(p, f"{_W}r")
        t = ET.SubElement(r, f"{_W}t")
        t.text = content

    paragraph(body, title, heading=True)
    paragraph(body, text)
    paragraph(
        body,
        "All engineering specifications conform strictly to the unidirectional architectural layer hierarchy. "
        "The automated judge evaluation suite verifies semantic integrity across all extracted knowledge graph assertions. "
        "Production release criteria require zero cycle dependencies and complete isolation of persistent credentials.",
    )

    table = ET.SubElement(body, f"{_W}tbl")
    for r in range(4):
        tr = ET.SubElement(table, f"{_W}tr")
        for c in range(3):
            tc = ET.SubElement(tr, f"{_W}tc")
            paragraph(tc, f"Cell R{r}C{c}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        zf.writestr("word/document.xml", ET.tostring(document, encoding="utf-8"))
    return buf.getvalue()


def _build_xlsx_bytes(title: str, text: str) -> bytes:
    """Construct a clean, valid XLSX byte stream."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ArchitectureSummary"
    ws.append(["Metric", "Description", "Status", "Owner"])
    ws.append(["Core Storage", "Transactions verified under write-ahead logging with serialized commit protocols", "Approved", "Storage Team"])
    ws.append(["Layer Integrity", "Comprehensive audit of all data pipelines ensures strict conformance to unidirectional layer boundaries", "Approved", "Architecture Team"])
    ws.append(["Retrieval Engine", "Evaluations demonstrate high retrieval accuracy across hybrid sparse and dense embedding models with zero lock contention", "Approved", "Search Team"])
    ws.append(["Deployment Gate", "Operational approval is confirmed for production release deployment following final stakeholder review", "Approved", "Release Team"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestParallelDocumentIngestionE2E(unittest.TestCase):
    """End-to-end tests for governed parallel document ingestion across multi-format batches."""

    @classmethod
    def setUpClass(cls) -> None:
        from dual_pipeline.index_core.backends import get_backend
        cls._shared_backend = get_backend("bge-m3-dev", allow_dev_backend=True)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.docs_dir = self.root / "inputs"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.out_root = self.root / "outputs"
        self.out_root.mkdir(parents=True, exist_ok=True)

        # 1. Markdown document
        self.md_file = self.docs_dir / "memo_apollo.md"
        self.md_file.write_text(
            "# Project Apollo Strategic Architecture Briefing\n\n"
            "This strategic memorandum outlines the operational readiness and architectural governance standards for Project Apollo. "
            "The engineering teams have completed the comprehensive audit of all data pipelines, ensuring strict conformance "
            "to unidirectional layer boundaries and zero cyclic dependencies across all modules. "
            "All database transactions are now verified under write-ahead logging with serialized commit protocols. "
            "Performance evaluations demonstrate high retrieval accuracy across hybrid sparse and dense embedding models. "
            "Operational approval is confirmed for release deployment following final stakeholder review.\n",
            encoding="utf-8",
        )

        # 2. DOCX document
        self.docx_file = self.docs_dir / "specs_artemis.docx"
        self.docx_file.write_bytes(
            _build_docx_bytes(
                "Project Artemis Specifications",
                "Artemis delivers autonomous multi-agent pipeline orchestration and high-throughput document processing.",
            )
        )

        # 3. XLSX workbook
        self.xlsx_file = self.docs_dir / "metrics_athena.xlsx"
        self.xlsx_file.write_bytes(
            _build_xlsx_bytes(
                "Athena Metrics Report",
                "Athena benchmarks show 99.4% precision with zero lock contention.",
            )
        )

        # 4. PDF document
        real_pdf = ROOT / "documents" / "hrit" / "HR360_PRF_Current_vs_Future_State.pdf"
        self.pdf_file = self.docs_dir / "charter_zeus.pdf"
        self.pdf_file.write_bytes(real_pdf.read_bytes())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parallel_ingestion_four_formats_end_to_end(self) -> None:
        """Four heterogeneous documents (.md, .docx, .xlsx, .pdf) ingest in parallel with staged vectors."""
        documents = [self.md_file, self.docx_file, self.xlsx_file, self.pdf_file]

        with patch("dual_pipeline.retrieval_release.validate_activation_evaluation", return_value={"required": False}):
            batch_result = ingest_documents_parallel(
                documents=documents,
                max_workers=4,
                provider_type="offline",
                output_dir=str(self.out_root),
                mutate_graph=False,
                auto_mutate_on_pass=False,
                group_commit=False,
                embedding_backend=self._shared_backend,
            )

        self.assertEqual(batch_result["status"], "COMPLETE")
        self.assertEqual(batch_result["total_documents"], 4)
        self.assertEqual(batch_result["successful_count"], 4)
        self.assertEqual(batch_result["failed_count"], 0)
        self.assertGreater(batch_result["duration_seconds"], 0.0)

        # Verify artifacts for every document
        for doc_path in documents:
            doc_str = str(doc_path.resolve())
            self.assertIn(doc_str, batch_result["results"])
            run_res = batch_result["results"][doc_str]
            self.assertIn(run_res.get("status"), ("PACKAGE_SEALED", "COMPLETE"))

            out_dir = Path(run_res["output_dir"])
            self.assertTrue(out_dir.is_dir())

            # Deliverable 03 must exist
            d03_files = list(out_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertGreaterEqual(len(d03_files), 1)

            # Metadata and intake package must exist
            self.assertTrue((out_dir / "metadata.json").is_file())
            self.assertTrue((out_dir / "intake_package.json").is_file())

            # Deliverables 01 and 02 must NOT exist for documents (Output 03 only)
            self.assertEqual(len(list(out_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(out_dir.glob("02_*"))), 0)

            # Staged vector database must exist in output directory
            staged_db = out_dir / "vector_staging.sqlite"
            self.assertTrue(
                staged_db.is_file(),
                f"Expected vector_staging.sqlite in {out_dir}",
            )
            staged_conn = sqlite3.connect(staged_db)
            staged_count = staged_conn.execute("SELECT count(*) FROM staged_dense_vector").fetchone()[0]
            staged_conn.close()
            self.assertGreater(staged_count, 0, "Vector staging DB must contain encoded vectors")

    def test_parallel_ingestion_group_commit_mode(self) -> None:
        """Batch documents ingest in parallel and atomic group-commit via prepare_many."""
        documents = [self.md_file, self.docx_file]

        with patch("dual_pipeline.retrieval_release.validate_activation_evaluation", return_value={"required": False}):
            batch_result = ingest_documents_parallel(
                documents=documents,
                max_workers=2,
                provider_type="offline",
                output_dir=str(self.out_root),
                group_commit=True,
                embedding_backend=self._shared_backend,
            )

        self.assertEqual(batch_result["status"], "COMPLETE")
        self.assertEqual(batch_result["successful_count"], 2)
        gc = batch_result.get("results", {}).get("_group_commit")
        self.assertIsNotNone(gc)
        self.assertNotEqual(gc.get("status"), "FAILED", f"Group commit failed: {gc.get('error')}")

    def test_cli_parallel_workers_dispatch(self) -> None:
        """CLI ingest-document with --inputs and --parallel-workers routes to parallel orchestrator."""
        import argparse
        args = argparse.Namespace(
            command="ingest-document",
            input=None,
            inputs=[self.md_file, self.docx_file],
            parallel_workers=2,
            provider="offline",
            output_dir=str(self.out_root),
            group_commit=False,
            mutate_graph=False,
            auto_mutate_on_pass=False,
            embedding_backend=self._shared_backend,
        )

        with patch("dual_pipeline.retrieval_release.validate_activation_evaluation", return_value={"required": False}):
            res = ingest_canonical(args)

        self.assertIsNotNone(res)
        self.assertEqual(res.get("status"), "COMPLETE")
        self.assertEqual(res.get("total_documents"), 2)


if __name__ == "__main__":
    unittest.main()
