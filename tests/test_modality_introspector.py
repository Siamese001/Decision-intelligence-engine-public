"""Unit tests for Structural & Modality Introspector."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.domain_models import ModalityScope
from tools.pr_governance_core.modality_introspector import (
    introspect_deliverable,
    introspect_markdown_documents,
    introspect_modality,
    introspect_python_code,
)

ROOT = Path(__file__).resolve().parents[1]


class TestModalityIntrospector(unittest.TestCase):
    def test_document_deliverable_metadata_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            out_dir = tmp_root / "output" / "project_nexus" / "10-04-2026" / "Energy_Utilities_Market_Antigravity"
            out_dir.mkdir(parents=True)
            meta = {
                "project": "project_nexus",
                "title": "Weekly Energy & Utilities Market Trends - 10/04/2026",
                "source_type": "document",
                "canonical_modality": "DOCUMENT",
                "is_document": True,
            }
            (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            (out_dir / "03_canonical_signal_projection.json").write_text("{}", encoding="utf-8")

            files = [
                "output/project_nexus/10-04-2026/Energy_Utilities_Market_Antigravity/metadata.json",
                "output/project_nexus/10-04-2026/Energy_Utilities_Market_Antigravity/03_canonical_signal_projection.json",
            ]
            res = introspect_deliverable(files, tmp_root)
            self.assertIsNotNone(res)
            self.assertEqual(res.modality, ModalityScope.DOCUMENT_WRITTEN)
            self.assertTrue(res.is_deliverable)
            self.assertEqual(res.source_type, "document")
            self.assertNotEqual(res.modality, ModalityScope.MEETING_SPOKEN)

    def test_meeting_deliverable_metadata_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            out_dir = tmp_root / "output" / "project_nexus" / "09-28-2026" / "Team_Sync"
            out_dir.mkdir(parents=True)
            meta = {
                "project": "project_nexus",
                "meeting_name": "Team Weekly Sync",
                "source_type": "audio",
                "canonical_modality": "AUDIO",
                "is_document": False,
                "audio_file": "recording.wav",
            }
            (out_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")

            files = ["output/project_nexus/09-28-2026/Team_Sync/metadata.json"]
            res = introspect_deliverable(files, tmp_root)
            self.assertIsNotNone(res)
            self.assertEqual(res.modality, ModalityScope.MEETING_SPOKEN)
            self.assertTrue(res.is_deliverable)
            self.assertEqual(res.source_type, "audio")

    def test_markdown_document_introspection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            doc_dir = tmp_root / "documents" / "market_intelligence"
            doc_dir.mkdir(parents=True)
            doc_content = (
                "# Weekly Energy & Utilities Market Trends Update - 10/04/2026\n"
                "**Publication Date:** 10/04/2026\n\n"
                "## 1. Executive Summary\n"
                "Apex Power & Grid maintains base rate stability across 2026-2029.\n"
                "Decision Intelligence Framing: Hyperscale Colocation.\n"
            )
            (doc_dir / "weekly_update_10-04-2026.md").write_text(doc_content, encoding="utf-8")

            files = ["documents/market_intelligence/weekly_update_10-04-2026.md"]
            res = introspect_markdown_documents(files, tmp_root)
            self.assertIsNotNone(res)
            self.assertEqual(res.modality, ModalityScope.DOCUMENT_WRITTEN)
            self.assertEqual(res.title, "Weekly Energy & Utilities Market Trends Update - 10/04/2026")
            self.assertIn("thought_leadership", res.topics)
            self.assertIn("decision_intelligence", res.topics)

    def test_python_code_ast_introspection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            tools_dir = tmp_root / "tools" / "pr_governance_core"
            tools_dir.mkdir(parents=True)
            code = (
                "from tools.run_ci_gates import run_gate\n"
                "import pr_priority_facts\n"
                "def audit(): pass\n"
            )
            (tools_dir / "gate_check.py").write_text(code, encoding="utf-8")

            files = ["tools/pr_governance_core/gate_check.py"]
            res = introspect_python_code(files, tmp_root)
            self.assertIsNotNone(res)
            self.assertEqual(res.modality, ModalityScope.CODE_GOVERNANCE)

    def test_introspect_modality_empty_and_fallback(self):
        res = introspect_modality([], Path.cwd())
        self.assertEqual(res.modality, ModalityScope.UNKNOWN)
        self.assertFalse(res.is_deliverable)


if __name__ == "__main__":
    unittest.main()
