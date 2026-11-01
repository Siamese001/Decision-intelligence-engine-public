"""Tests for Document and Email Ingestion Pipeline (Output 03 Only).

Verifies:
1. scaffold_document_metadata extracts headers, attendees, dates, and flags output3_only.
2. evaluate_document_qa_gates gates Gate 3 strictly against source text while marking Gates 1 & 2 N/A.
3. DocumentPipeline emits strictly Deliverable 03 and governance receipts without 01 or 02.
4. validate_output_hierarchy validates Output 03-only document directories.
5. CLI commands `nee ingest-document` and `nee ingest-meeting --document` route seamlessly.
6. Ingestion of the Petter Skantze NEE Technology Org announcement email.
7. Multi-format ingestion for Word (.docx), PDF (.pdf), Markdown (.md), and PowerPoint (.pptx).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline import (
    DocumentGraphExtractorExecutor,
    DocumentPipeline,
    evaluate_document_qa_gates,
    scaffold_document_metadata,
)
from tools.validate_output_hierarchy import validate_meeting_directory


class TestDocumentEmailIngestion(unittest.TestCase):

    def test_scaffold_document_metadata(self) -> None:
        email_text = (
            "From: Petter Skantze <Employee-Communications@ec.nee.com>\n"
            "Date: Wednesday, September 16, 2026 at 8:45 AM\n"
            "To: Medina, Jose <JOSE.MEDINA@enterprise.example.com>\n"
            "Subject: Coming together as one Technology organization\n\n"
            "Team,\n"
            "Today, we are announcing a transformation in how our technology teams are organized.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_file = Path(tmpdir) / "email.txt"
            doc_file.write_text(email_text, encoding="utf-8")

            meta = scaffold_document_metadata(doc_file, project="enterprise_architecture")

            self.assertEqual(meta["title"], "Coming together as one Technology organization")
            self.assertEqual(meta["author"], "Petter Skantze")
            self.assertIn("Petter Skantze", meta["attendees"])
            self.assertIn("Medina, Jose", meta["attendees"])
            self.assertEqual(meta["date"], "09/16/2026")
            self.assertEqual(meta["date_filename"], "09-16-2026")
            self.assertEqual(meta["project"], "enterprise_architecture")
            self.assertTrue(meta["is_document"])
            self.assertTrue(meta["output3_only"])

    def test_evaluate_document_qa_gates(self) -> None:
        from dual_pipeline.document_pipeline_core.executor import DocumentGraphExtractorExecutor
        from dual_pipeline.transcript_stages_core.synthesizers import OfflineTranscriptSynthesizer

        source_text = (
            "Today we announce that IT and NEA are combining into one Technology organization. "
            "Ben Miron will serve as Chief Information Officer leading core tech. "
            "Rich Argentieri will serve as Chief Product Officer leading products."
        )
        meta = {
            "project": "enterprise_architecture",
            "date": "09/16/2026",
            "date_filename": "09-16-2026",
            "title": "Org Transformation",
            "classification": "internal",
            "audiences": ["internal_consulting"],
        }
        executor = DocumentGraphExtractorExecutor(OfflineTranscriptSynthesizer())
        projection_str = executor.execute(source_text, pa_context={"metadata": meta})

        report = evaluate_document_qa_gates(projection_str, source_text=source_text, metadata=meta)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertEqual(report["gate_1"]["reason"], "NOT_APPLICABLE_DOCUMENT_MODE")
        self.assertEqual(report["gate_2"]["reason"], "NOT_APPLICABLE_DOCUMENT_MODE")
        self.assertEqual(report["gate_3"]["status"], "PASS")

    def test_document_pipeline_e2e_offline(self) -> None:
        email_text = (
            "From: Leadership <leader@company.com>\n"
            "Date: 09/16/2026\n"
            "Subject: Enterprise Cloud Modernization Policy\n\n"
            "Executive Leadership announces that all cloud deployments must strictly use AWS us-east-1. "
            "Jane Doe is appointed Lead Cloud Architect. "
            "John Smith will oversee Security and Governance. " + "This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. "*3
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            doc_file = tmp_path / "cloud_policy.txt"
            doc_file.write_text(email_text, encoding="utf-8")

            out_root = tmp_path / "output"

            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(doc_file),
                output_dir=str(out_root),
                metadata={"package_only": True},
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])
            self.assertTrue(dest_dir.exists())

            # Check that NO 01 or 02 exist
            files_01 = list(dest_dir.glob("01_*"))
            files_02 = list(dest_dir.glob("02_*"))
            self.assertEqual(len(files_01), 0)
            self.assertEqual(len(files_02), 0)

            # Check that 03 and receipts exist
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            self.assertTrue((dest_dir / "metadata.json").exists())
            self.assertTrue((dest_dir / "qa_gates_report.json").exists())
            self.assertTrue((dest_dir / "llm_judge_evaluation.json").exists())
            self.assertTrue((dest_dir / "final_release_decision.json").exists())
            self.assertTrue((dest_dir / "intake_package.json").exists())

            # Check that attempt-level diagnostics are flushed to disk
            attempt_1_dir = dest_dir / "attempts" / "attempt_1"
            self.assertTrue(attempt_1_dir.exists())
            self.assertTrue((attempt_1_dir / "qa_gates_report.json").exists())
            self.assertTrue((attempt_1_dir / "llm_judge_evaluation.json").exists())
            self.assertTrue((attempt_1_dir / "final_release_decision.json").exists())
            self.assertEqual(len(list(attempt_1_dir.glob("03_*_canonical_signal_projection.json"))), 1)

            # Validate directory hierarchy compliance
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

    def test_cli_ingest_document_offline(self) -> None:
        from dual_pipeline.cli.engine import main as cli_main

        doc_content = (
            "From: Petter Skantze <Employee-Communications@ec.nee.com>\n"
            "Date: Wednesday, September 16, 2026 at 8:45 AM\n"
            "Subject: Org Announcement\n\n"
            "Today IT and NEA combine into Technology org. " + "This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. "*3 + "\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            doc_file = tmp_path / "memo.txt"
            doc_file.write_text(doc_content, encoding="utf-8")
            out_root = tmp_path / "output"

            ret = cli_main([
                "ingest-document",
                "--input", str(doc_file),
                "--output-dir", str(out_root),
                "--provider", "offline", "--project", "internal_consulting",
                "--package-only",
            ])

            self.assertEqual(ret, 0)
            target_subdirs = list(out_root.glob("*/*/*")) or list(out_root.glob("*/*"))
            self.assertGreaterEqual(len(target_subdirs), 1)
            sub = target_subdirs[0]
            self.assertEqual(len(list(sub.glob("03_*"))), 1)
            self.assertEqual(len(list(sub.glob("01_*"))), 0)
            self.assertEqual(len(list(sub.glob("02_*"))), 0)

    def test_cli_ingest_meeting_document_flag_offline(self) -> None:
        from dual_pipeline.cli.engine import main as cli_main

        doc_content = (
            "From: Petter Skantze <Employee-Communications@ec.nee.com>\n"
            "Date: Wednesday, September 16, 2026 at 8:45 AM\n"
            "Subject: Ingest Meeting Flag Test\n\n"
            "Testing delegation from ingest-meeting with --document flag. " + "This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. "*3 + "\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            doc_file = tmp_path / "memo.txt"
            doc_file.write_text(doc_content, encoding="utf-8")
            out_root = tmp_path / "output"

            ret = cli_main([
                "ingest-meeting",
                "--document",
                "--input", str(doc_file),
                "--output-dir", str(out_root),
                "--provider", "offline", "--project", "internal_consulting",
                "--package-only",
            ])

            self.assertEqual(ret, 0)
            target_subdirs = list(out_root.glob("*/*/*")) or list(out_root.glob("*/*"))
            self.assertGreaterEqual(len(target_subdirs), 1)
            sub = target_subdirs[0]
            self.assertEqual(len(list(sub.glob("03_*"))), 1)
            self.assertEqual(len(list(sub.glob("01_*"))), 0)
            self.assertEqual(len(list(sub.glob("02_*"))), 0)

    def test_real_email_petter_skantze_offline(self) -> None:
        real_email_path = Path("intake/2026-09-16_NEE_Technology_Org_Announcement.txt")
        self.assertTrue(real_email_path.exists(), "Pristine email intake file must exist")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_root = Path(tmpdir) / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(real_email_path),
                output_dir=str(out_root),
                metadata={"package_only": True},
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])

            # Validate hierarchy
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

            # Validate 03 payload contains core entities
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            data_03 = json.loads(files_03[0].read_text(encoding="utf-8"))

            # Verify canonical schema structure in output 03
            self.assertEqual(data_03.get("schema_version"), "5.7")
            self.assertIn("graph_mutations", data_03)
            self.assertIn("evidence_index", data_03)
            self.assertEqual(data_03.get("classification"), "internal")

    def test_docx_document_ingestion_e2e(self) -> None:
        import zipfile
        xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t># Unified Cloud Modernization Charter</w:t></w:r></w:p>
    <w:p><w:r><w:t>Date: 09/16/2026</w:t></w:r></w:p>
    <w:p><w:r><w:t>Author: Enterprise Architecture Committee</w:t></w:r></w:p>
    <w:p><w:r><w:t>All core workloads will migrate to governed cloud landing zones by Q4 2026. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. </w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_file = Path(tmpdir) / "cloud_charter.docx"
            with zipfile.ZipFile(docx_file, "w") as z:
                z.writestr("word/document.xml", xml)

            out_root = Path(tmpdir) / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(docx_file),
                output_dir=str(out_root),
                metadata={"package_only": True},
                project="internal_consulting",
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])
            self.assertEqual(len(list(dest_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(dest_dir.glob("02_*"))), 0)
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

    def test_pdf_document_ingestion_e2e(self) -> None:
        earnings_pdf = Path("inputs/rewire_earnings_2026/apex_global-q1-2026-earnings-script.pdf")
        self.assertTrue(earnings_pdf.exists(), "Sample earnings PDF must exist")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_root = Path(tmpdir) / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(earnings_pdf),
                output_dir=str(out_root),
                metadata={"date": "09/16/2026", "package_only": True},
                project="internal_consulting",
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])
            self.assertEqual(len(list(dest_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(dest_dir.glob("02_*"))), 0)
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

    def test_markdown_document_ingestion_e2e(self) -> None:
        md_content = (
            "# Architecture Decision: Distributed Cache Strategy\n\n"
            "Date: 09/16/2026\n"
            "Author: Chief Architect\n\n"
            "We decide to deploy Redis cluster for hot session data across all regions. " + "This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. "*3 + "\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            md_file = Path(tmpdir) / "cache_adr.md"
            md_file.write_text(md_content, encoding="utf-8")

            out_root = Path(tmpdir) / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(md_file),
                output_dir=str(out_root),
                metadata={"package_only": True},
                project="internal_consulting",
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])
            self.assertEqual(len(list(dest_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(dest_dir.glob("02_*"))), 0)
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

    def test_pptx_presentation_ingestion_e2e(self) -> None:
        from tests.test_pptx_source import _create_synthetic_pptx_bytes

        with tempfile.TemporaryDirectory() as tmpdir:
            pptx_file = Path(tmpdir) / "strategy.pptx"
            pptx_file.write_bytes(_create_synthetic_pptx_bytes())

            out_root = Path(tmpdir) / "output"
            pipeline = DocumentPipeline(provider_type="offline")
            res = pipeline.run(
                str(pptx_file),
                output_dir=str(out_root),
                metadata={"date": "09/16/2026", "package_only": True},
                project="internal_consulting",
            )

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            dest_dir = Path(res["output_dir"])
            self.assertEqual(len(list(dest_dir.glob("01_*"))), 0)
            self.assertEqual(len(list(dest_dir.glob("02_*"))), 0)
            files_03 = list(dest_dir.glob("03_*_canonical_signal_projection.json"))
            self.assertEqual(len(files_03), 1)
            hier_res = validate_meeting_directory(dest_dir)
            self.assertEqual(len(hier_res), 0, f"Hierarchy violations: {hier_res}")

    def test_cli_ingest_document_multi_format(self) -> None:
        from dual_pipeline.cli.engine import main as cli_main
        md_content = "# Data Mesh Principles\n\nDate: 09/16/2026\n\nData products are domain owned. " + "This is additional filler text to ensure the document exceeds the fifty word minimum threshold for semantic yield testing. "*3 + "\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            md_file = tmp_path / "datamesh.md"
            md_file.write_text(md_content, encoding="utf-8")
            out_root = tmp_path / "output"

            ret = cli_main([
                "ingest-document",
                "--input", str(md_file),
                "--output-dir", str(out_root),
                "--provider", "offline", "--project", "internal_consulting",
                "--package-only",
            ])
            self.assertEqual(ret, 0)
            target_subdirs = list(out_root.glob("*/*/*")) or list(out_root.glob("*/*"))
            self.assertGreaterEqual(len(target_subdirs), 1)
            sub = target_subdirs[0]
            self.assertEqual(len(list(sub.glob("03_*"))), 1)
            self.assertEqual(len(list(sub.glob("01_*"))), 0)
            self.assertEqual(len(list(sub.glob("02_*"))), 0)

    def test_decoupled_document_graph_extractor_executor(self) -> None:
        from dual_pipeline.document_pipeline_core.executor import _build_document_user_content
        from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY
        from dual_pipeline.transcript_stages_core.synthesizers import OfflineTranscriptSynthesizer

        # 1. Verify SSOT prompt is registered and accessible
        prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_DOCUMENT_GRAPH")
        self.assertIn("STAGE 03 — DOCUMENT GRAPH EXTRACTOR", prompt)
        self.assertIn("EPISTEMIC AUTHORITY", prompt)

        # 2. Verify native document tag formatting
        meta = {
            "title": "Org Transformation",
            "author": "Petter Skantze",
            "date": "09/16/2026",
            "classification": "internal",
        }
        content = _build_document_user_content("Announcement text body.", meta, remediation_directive="Focus on appointments.")
        self.assertIn("<document_metadata>", content)
        self.assertIn("<document_source>\nAnnouncement text body.\n</document_source>", content)
        self.assertIn("<remediation_directive>\nFocus on appointments.\n</remediation_directive>", content)

        # 3. Verify executor offline synthesis produces valid Output 03
        executor = DocumentGraphExtractorExecutor(OfflineTranscriptSynthesizer())
        raw_res = executor.execute("Announcement text body.", pa_context={"metadata": meta})
        data = json.loads(raw_res)
        self.assertEqual(data.get("schema_version"), "5.7")
        self.assertIn("graph_mutations", data)


if __name__ == "__main__":
    unittest.main()
