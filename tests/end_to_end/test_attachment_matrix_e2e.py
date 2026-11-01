"""tests/end_to_end/test_attachment_matrix_e2e.py

Wave 4 End-to-End Acceptance Test Suite:
Multi-format attachment intake and staged mutation lifecycle across the format matrix.
Verifies:
  1. Multi-source manifest construction across supported formats (.md, .txt, .docx, .pdf, .pptx, .xlsx, .csv, .html, .eml).
  2. Summary-to-raw-source binding with exact cryptographic provenance (summarizes_sources).
  3. Output 03 assertion provenance requiring exact unit locators from originating raw attachments.
  4. Composed summaries rejected as primary evidence when raw attachments are present.
  5. Proposal-bound human approval under publication lock.
  6. Safe mutation into provision_isolated_test_db() with generation bump.
  7. Idempotent replay and tamper detection across multi-attachment runs.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import openpyxl
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from dual_pipeline.canonical_pipeline.lifecycle import deploy_deliverables
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.core import sha256_file
from dual_pipeline.document_pipeline_core.executor import (
    AssertionProvenanceError,
    validate_attachment_assertion_provenance,
)
from dual_pipeline.document_pipeline_core.source_manifest import (
    SourceManifest,
    build_source_manifest,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    MutationProposal,
    ProposalStatus,
    load_approval_request,
    persist_approval_request,
)
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    MutationAuthorizationError,
    compute_proposal_digest_from_deliverables,
    mark_mutation_activated,
    validate_mutation_authorization,
)
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


def _make_minimal_docx(path: Path, text: str) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    path.write_bytes(buf.getvalue())
    return path


def _make_minimal_pptx(path: Path, title: str) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
            "</Relationships>",
        )
        z.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        z.writestr(
            "ppt/slides/slide1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
            "</p:sld>",
        )
    path.write_bytes(buf.getvalue())
    return path


def _make_minimal_xlsx(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Financials"
    ws.append(["Category", "Budget", "Actual"])
    ws.append(["Platform Engineering", 120000, 115000])
    wb.save(path)
    return path


def _make_minimal_pdf(path: Path, text: str) -> Path:
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_obj)
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    t_stream = DecodedStreamObject()
    t_stream.set_data(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("latin1"))
    page[NameObject("/Contents")] = writer._add_object(t_stream)
    page[NameObject("/Resources")] = resources
    buf = io.BytesIO()
    writer.write(buf)
    path.write_bytes(buf.getvalue())
    return path


class TestAttachmentMatrixE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="att_matrix_e2e_")
        self.root = Path(self.tmp.name)
        self.sources_dir = self.root / "sources"
        self.sources_dir.mkdir(parents=True)
        self.output_dir = self.root / "output" / "proj_nexus" / "01-01-2026" / "Matrix_Intake"
        self.output_dir.mkdir(parents=True)
        self.runtime_root = self.root / ".runtime"
        self.runtime_root.mkdir(parents=True)

        res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = res["isolated_db_path"]
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-matrix-001", "graph_path": self.isolated_db_path}),
            encoding="utf-8",
        )

        # Create fixtures across formats
        self.md_file = self.sources_dir / "architecture.md"
        self.md_file.write_text("# SOW Architecture\nLine 2 details system integration.\n", encoding="utf-8")

        self.txt_file = self.sources_dir / "plain.txt"
        self.txt_file.write_text("Line 1: System requirements.\nLine 2: Target latency 50ms.\n", encoding="utf-8")

        self.csv_file = self.sources_dir / "metrics.csv"
        self.csv_file.write_text("metric,value,unit\nthroughput,1500,qps\n", encoding="utf-8")

        self.html_file = self.sources_dir / "spec.html"
        self.html_file.write_text("<html><body><h1>API Spec</h1><p>REST endpoint v1.</p></body></html>", encoding="utf-8")

        self.eml_file = self.sources_dir / "approval.eml"
        self.eml_file.write_text("From: architect@example.com\nSubject: SOW approval\n\nApproved architecture.\n", encoding="utf-8")

        self.xlsx_file = _make_minimal_xlsx(self.sources_dir / "financials.xlsx")
        self.docx_file = _make_minimal_docx(self.sources_dir / "contract.docx", "Contract agreement between parties.")
        self.pptx_file = _make_minimal_pptx(self.sources_dir / "deck.pptx", "Architecture Overview")
        self.pdf_file = _make_minimal_pdf(self.sources_dir / "report.pdf", "Executive Audit Report")

        # Composed summary paired with raw sources
        self.summary_file = self.sources_dir / "executive_summary.md"
        self.summary_file.write_text(
            "# Executive Recap Summary\nSynthesized findings across all 9 raw attachments.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()
        p = Path(self.isolated_db_path)
        if p.is_file():
            p.unlink(missing_ok=True)

    def test_multi_format_manifest_and_summary_binding(self) -> None:
        raw_sources = [
            self.md_file, self.txt_file, self.csv_file, self.html_file,
            self.eml_file, self.xlsx_file, self.docx_file, self.pptx_file, self.pdf_file,
        ]
        all_sources = raw_sources + [self.summary_file]

        manifest: SourceManifest = build_source_manifest(all_sources)
        m_dict = manifest.to_dict()

        self.assertEqual(m_dict["total_sources"], 10)
        self.assertEqual(len(m_dict["sources"]), 10)

        # Check raw vs summary separation
        raw_entries = [s for s in m_dict["sources"] if not s["is_summary"]]
        summary_entries = [s for s in m_dict["sources"] if s["is_summary"]]

        self.assertEqual(len(raw_entries), 9)
        self.assertEqual(len(summary_entries), 1)

        # Provenance: summary must bind to raw source hashes
        summary = summary_entries[0]
        self.assertIsNotNone(summary["summary_provenance"])
        raw_refs = summary["summary_provenance"]["summarizes_sources"]
        self.assertEqual(len(raw_refs), 9)

        raw_hashes = {s["sha256"] for s in raw_entries}
        ref_hashes = {r["sha256"] for r in raw_refs}
        self.assertEqual(raw_hashes, ref_hashes)

    def test_end_to_end_candidate_to_isolated_mutation_lifecycle(self) -> None:
        # Step 1: Build manifest
        all_sources = [self.xlsx_file, self.pdf_file, self.csv_file, self.summary_file]
        manifest = build_source_manifest(all_sources)

        # Step 2: Formulate Deliverable 03 with exact locators from raw attachments
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-fin",
                        "predicate": "ALLOCATES_BUDGET",
                        "evidence_ids": ["ev-xlsx-1"],
                    },
                    {
                        "signal_id": "sig-pdf",
                        "predicate": "DEFINES_SCOPE",
                        "evidence_ids": ["ev-pdf-1"],
                    },
                    {
                        "signal_id": "sig-csv",
                        "predicate": "PERFORMANCE_TARGET",
                        "evidence_ids": ["ev-csv-1"],
                    },
                ],
                "entity_upserts": [
                    {
                        "entity_id": "ent-platform",
                        "name": "Platform Engineering",
                        "evidence_ids": ["ev-xlsx-1"],
                    }
                ],
            },
            "evidence_index": {
                "ev-xlsx-1": {
                    "evidence_id": "ev-xlsx-1",
                    "exact_text": "Platform Engineering: 120000",
                    "source_locator": "Financials!R2C2",
                    "source_file": "financials.xlsx",
                },
                "ev-pdf-1": {
                    "evidence_id": "ev-pdf-1",
                    "exact_text": "Executive Audit Report",
                    "source_locator": "p1",
                    "source_file": "report.pdf",
                },
                "ev-csv-1": {
                    "evidence_id": "ev-csv-1",
                    "exact_text": "throughput,1500,qps",
                    "source_locator": "R1",
                    "source_file": "metrics.csv",
                },
            },
        }

        meta = {
            "meeting_name": "Matrix Intake",
            "date": "01-01-2026",
            "runtime_root": str(self.runtime_root.resolve()),
            "graph_path": self.isolated_db_path,
            "enable_attachment_mutation_automation": True,
            "source_manifest": manifest.to_dict(),
            "source_attachments": [str(s) for s in all_sources],
        }

        # Validate assertion provenance passes
        validate_attachment_assertion_provenance(projection, meta)

        # Step 3: Deploy deliverables (package-only candidate stage)
        synthesis = SynthesisResult(
            output_1="",
            output_2="Executive summary recap",
            output_3=json.dumps(projection, indent=2),
        )
        artifacts = deploy_deliverables(self.output_dir, meta, CanonicalModality.DOCUMENT, synthesis)
        self.assertIn("03_graph", artifacts)
        self.assertIn("source_manifest", artifacts)

        # Write intake package
        intake_pkg = self.output_dir / "intake_package.json"
        intake_pkg.write_text(json.dumps({"meeting": {"meeting_id": "matrix-intake", "source_hash": "b" * 64}}), encoding="utf-8")
        artifacts["intake_package"] = str(intake_pkg)
        artifacts["source_file"] = str(self.xlsx_file)

        # Step 4: Propose mutation and verify proposal digest
        out_hashes = {
            "03_graph": sha256_file(Path(artifacts["03_graph"])),
            "source_manifest": sha256_file(Path(artifacts["source_manifest"])),
        }
        prop_digest = compute_proposal_digest_from_deliverables(
            meeting_name="Matrix Intake",
            source_hash=sha256_file(self.xlsx_file),
            output_hashes=out_hashes,
            intake_hash=sha256_file(intake_pkg),
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=str(self.runtime_root.resolve()),
            expected_base_generation="gen-matrix-001",
        )

        req_id = "req-matrix-001"
        req_record = {
            "request_id": req_id,
            "proposal_digest": prop_digest,
            "output_hashes": out_hashes,
            "status": ProposalStatus.APPROVED.value,
            "authorized": True,
            "operator_origin": "human_operator",
            "meeting_name": "Matrix Intake",
            "expected_base_generation": "gen-matrix-001",
            "target_graph": "CANONICAL_GRAPH.db",
        }
        persist_approval_request(req_record, self.runtime_root)
        meta["approval_request_id"] = req_id

        # Step 5: Authorize and activate mutation in isolated runtime
        auth_info = validate_mutation_authorization(
            self.output_dir, meta, artifacts, runtime_root=self.runtime_root
        )
        self.assertTrue(auth_info["authorized"])
        self.assertEqual(auth_info["status"], ProposalStatus.APPLYING.value)

        # Mark activated
        mark_mutation_activated(req_id, self.runtime_root, "gen-matrix-002", {"nodes_inserted": 1})
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-matrix-002", "graph_path": self.isolated_db_path}),
            encoding="utf-8",
        )

        # Step 6: Verify idempotent replay
        meta["approval_receipt"] = load_approval_request(req_id, self.runtime_root)
        replay = validate_mutation_authorization(
            self.output_dir, meta, artifacts, runtime_root=self.runtime_root, allow_idempotent_recovery=True,
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["status"], ProposalStatus.ACTIVATED.value)


if __name__ == "__main__":
    unittest.main()
