"""
tests/end_to_end/test_parallel_multi_modality_ingest_e2e.py

End-to-End Acceptance Test Suite for Wave 1:
Parallel Multi-Modality Ingestion Coordinator verifying:
  1. Parallel meeting ingestion generating all 3 outputs (01_transcript, 02_recap, 03_graph).
  2. Parallel document ingestion generating deliverable 03 only.
  3. Mixed parallel ingestion (meeting + document) where each routes to its true modality.
  4. Isolation verification: documents do not inherit meeting speaker maps or timestamps.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.document_pipeline_core.parallel_ingestor import ingest_documents_parallel


class TestParallelMultiModalityIngestE2E(unittest.TestCase):
    """Verifies parallel ingestion across meetings, documents, and mixed batches."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.inputs_dir = self.root / "inputs"
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.out_root = self.root / "outputs"
        self.out_root.mkdir(parents=True, exist_ok=True)

        # 1. Meeting source 1 (raw transcript format)
        self.meeting_1 = self.inputs_dir / "meeting_alpha.txt"
        self.meeting_1.write_text(
            "# Project LENS Live Meeting Feed\n\n"
            "**harper Hoffman:** [03:12] We reviewed the SSW with Amit Air and Solom. "
            "We checked the figlioli pitch pitch on the turbine at La Paz.\n"
            "**Amit Ayer:** [04:00] The rule creation tool had instability earlier this week in Dev.\n"
            "**harper Hoffman:** [07:12] Thank you everyone.\n",
            encoding="utf-8",
        )

        # 2. Meeting source 2 (raw transcript format)
        self.meeting_2 = self.inputs_dir / "meeting_beta.txt"
        self.meeting_2.write_text(
            "# Project LENS Sync Feed\n\n"
            "**harper Hoffman:** [03:12] Today we review the parallel multi-worker coordinator.\n"
            "**Amit Ayer:** [04:00] Each meeting task must generate transcript, recap, and graph deliverables.\n"
            "**harper Hoffman:** [07:12] The tests verify all three deliverables are emitted.\n",
            encoding="utf-8",
        )

        # 3. Document source 1 (markdown memo)
        self.doc_1 = self.inputs_dir / "memo_governance.md"
        self.doc_1.write_text(
            "# Governance Architecture Specification\n\n"
            "This document outlines the operational readiness and architectural governance standards for Project Nexus. "
            "The engineering teams have completed the comprehensive audit of all data pipelines, ensuring strict conformance "
            "to unidirectional layer boundaries and zero cyclic dependencies across all modules. "
            "All database transactions are now verified under write-ahead logging with serialized commit protocols. "
            "Performance evaluations demonstrate high retrieval accuracy across hybrid sparse and dense embedding models. "
            "Operational approval is confirmed for release deployment following final stakeholder review.\n",
            encoding="utf-8",
        )

        # 4. Document source 2 (markdown memo)
        self.doc_2 = self.inputs_dir / "memo_architecture.md"
        self.doc_2.write_text(
            "# Enterprise Architectural Blueprint\n\n"
            "This document establishes the canonical boundary principles for multi-tier data systems. "
            "All sub-pipelines must communicate strictly via unidirectional contracts without backward cycles. "
            "Storage access is strictly isolated using WAL mode and serialized transactions across all processes. "
            "Automated qualification benchmarks verify continuous alignment against operational performance targets.\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parallel_meetings_produce_all_three_deliverables(self) -> None:
        """Parallel meetings must emit 01_transcript, 02_recap, and 03_graph."""
        m_meta_base = {
            "date": "09/11/2026",
            "date_filename": "09-11-2026",
            "time": "04:32 PM - 04:37 PM ET",
            "time_filename": "0432PM_0437PM_ET",
            "duration_minutes": 5,
            "platform": "Microsoft Teams",
            "classification": "confidential",
            "audiences": ["nea-product", "partner_advisory-delivery", "project-lens-team", "internal_consulting", "nee-engineering"],
            "meeting_type": "EXECUTIVE_ALIGNMENT_REBASELINE",
            "attendees": {
                "Apex Global Enterprises (NEA)": ["harper Hoffman (Product Manager)"],
                "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
            },
            "roster": [
                {"name": "harper Hoffman", "role": "Product Manager"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            ],
            "expected_domain_terms": ["Bonfiglioli", "La Casa", "SteerCo"],
            "cutoff_marker": "**harper Hoffman:** [07:12]",
        }
        meetings = [
            {
                "path": self.meeting_1,
                "output_dir": str(self.out_root / "meeting_alpha"),
                "metadata": {**m_meta_base, "meeting_name": "Meeting_Alpha", "no_subfolder": True},
            },
            {
                "path": self.meeting_2,
                "output_dir": str(self.out_root / "meeting_beta"),
                "metadata": {
                    **m_meta_base,
                    "meeting_name": "Meeting_Beta",
                    "date": "09/12/2026",
                    "date_filename": "09-12-2026",
                    "time": "09:00 AM - 09:05 AM ET",
                    "time_filename": "0900AM_0905AM_ET",
                    "no_subfolder": True,
                },
            },
        ]
        res = ingest_documents_parallel(
            documents=meetings,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["total_documents"], 2)
        self.assertEqual(res["successful_count"], 2)

        for m_item in meetings:
            m_path = m_item["path"]
            m_res = res["results"][str(m_path.resolve())]
            out_p = Path(m_res["output_dir"])
            self.assertTrue(out_p.is_dir())
            d01 = list(out_p.glob("01_*"))
            d02 = list(out_p.glob("02_*"))
            d03 = list(out_p.glob("03_*_canonical_signal_projection.json"))
            self.assertGreaterEqual(len(d01), 1, f"Missing 01 deliverable in {out_p}")
            self.assertGreaterEqual(len(d02), 1, f"Missing 02 deliverable in {out_p}")
            self.assertGreaterEqual(len(d03), 1, f"Missing 03 deliverable in {out_p}")

    def test_parallel_documents_produce_graph_deliverables_only(self) -> None:
        """Parallel documents must emit deliverable 03 only, with zero speaker map or 01/02 files."""
        docs = [
            {
                "path": self.doc_1,
                "output_dir": str(self.out_root / "doc_1_parallel"),
                "metadata": {"no_subfolder": True},
            },
            {
                "path": self.doc_2,
                "output_dir": str(self.out_root / "doc_2_parallel"),
                "metadata": {"no_subfolder": True},
            },
        ]
        res = ingest_documents_parallel(
            documents=docs,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["total_documents"], 2)
        self.assertEqual(res["successful_count"], 2)

        for d_item in docs:
            d_path = d_item["path"]
            d_res = res["results"][str(d_path.resolve())]
            out_p = Path(d_res["output_dir"])
            self.assertTrue(out_p.is_dir())
            # Deliverable 01 and 02 must NOT exist for documents
            self.assertEqual(len(list(out_p.glob("01_*"))), 0, f"Unexpected 01 deliverable in {out_p}")
            self.assertEqual(len(list(out_p.glob("02_*"))), 0, f"Unexpected 02 deliverable in {out_p}")
            # Deliverable 03 must exist
            d03 = list(out_p.glob("03_*_canonical_signal_projection.json"))
            self.assertGreaterEqual(len(d03), 1, f"Missing 03 deliverable in {out_p}")
            # Metadata must mark is_document and have no speaker map
            meta_file = out_p / "metadata.json"
            self.assertTrue(meta_file.is_file())
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            self.assertTrue(meta.get("is_document"))
            self.assertFalse(meta.get("speaker_map"))

    def test_mixed_parallel_batch_routes_to_true_modalities(self) -> None:
        """Mixed batch: meetings emit 01/02/03; documents emit only 03."""
        m_meta_base = {
            "meeting_name": "Meeting_Alpha",
            "date": "09/11/2026",
            "date_filename": "09-11-2026",
            "time": "04:32 PM - 04:37 PM ET",
            "time_filename": "0432PM_0437PM_ET",
            "duration_minutes": 5,
            "platform": "Microsoft Teams",
            "classification": "confidential",
            "audiences": ["nea-product", "partner_advisory-delivery", "project-lens-team", "internal_consulting", "nee-engineering"],
            "meeting_type": "EXECUTIVE_ALIGNMENT_REBASELINE",
            "attendees": {
                "Apex Global Enterprises (NEA)": ["harper Hoffman (Product Manager)"],
                "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
            },
            "roster": [
                {"name": "harper Hoffman", "role": "Product Manager"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            ],
            "expected_domain_terms": ["Bonfiglioli", "La Casa", "SteerCo"],
            "cutoff_marker": "**harper Hoffman:** [07:12]",
        }
        mixed_batch = [
            {
                "path": self.meeting_1,
                "output_dir": str(self.out_root / "meeting_mixed"),
                "metadata": {**m_meta_base, "no_subfolder": True},
            },
            {
                "path": self.doc_1,
                "output_dir": str(self.out_root / "doc_mixed"),
                "metadata": {"no_subfolder": True},
            },
        ]
        res = ingest_documents_parallel(
            documents=mixed_batch,
            max_workers=2,
            provider_type="offline",
            output_dir=str(self.out_root),
            mutate_graph=False,
            auto_mutate_on_pass=False,
        )

        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["total_documents"], 2)

        # Verify meeting outputs
        m_res = res["results"][str(self.meeting_1.resolve())]
        m_out = Path(m_res["output_dir"])
        self.assertGreaterEqual(len(list(m_out.glob("01_*"))), 1)
        self.assertGreaterEqual(len(list(m_out.glob("02_*"))), 1)
        self.assertGreaterEqual(len(list(m_out.glob("03_*"))), 1)

        # Verify document outputs
        d_res = res["results"][str(self.doc_1.resolve())]
        d_out = Path(d_res["output_dir"])
        self.assertEqual(len(list(d_out.glob("01_*"))), 0)
        self.assertEqual(len(list(d_out.glob("02_*"))), 0)
        self.assertGreaterEqual(len(list(d_out.glob("03_*"))), 1)

        # Verify document metadata does not inherit meeting speaker maps
        d_meta_file = d_out / "metadata.json"
        self.assertTrue(d_meta_file.is_file())
        d_meta = json.loads(d_meta_file.read_text(encoding="utf-8"))
        self.assertFalse(d_meta.get("speaker_map"))
        self.assertTrue(d_meta.get("is_document"))


if __name__ == "__main__":
    unittest.main()
