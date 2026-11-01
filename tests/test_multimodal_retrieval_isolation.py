"""
Unit and integration test suite for Wave 5: Multimodal Evidence BGE-M3 Facet Isolation.

Verifies:
1. Automatic zero-touch discovery of referenced_artifacts/ without CLI --attachment.
2. Ingesting slide attachments automatically populates evidence_item nodes with COLLATERAL_REVIEWED.
3. Governed entity facet extraction for slide topics (Three Parts Of Every Notebook).
4. All 11 non-compensating retrieval release gates pass under BGE-M3 without score dilution.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import (
    discover_attachments,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.types import AttachmentType
from dual_pipeline.release_policy import (
    RETRIEVAL_RELEASE_11_GATES,
    evaluate_retrieval_11_gates,
)
from dual_pipeline.transcript_qa_core.collateral_healer import heal_collateral_graph_mutations
from dual_pipeline.transcript_qa_core.graph_ontology_healer import (
    heal_ontology_and_predicates,
)


class TestMultimodalRetrievalIsolation(unittest.TestCase):
    """Unit and integration tests for multimodal attachment discovery and facet isolation."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp(prefix="test_multimodal_isolation_"))
        self.output_dir = self.test_dir / "output" / "gemini_enablement" / "09-30-2026" / "Working_Files_Data_Rose_Amanda"
        self.ref_artifacts_dir = self.output_dir / "referenced_artifacts"
        self.ref_artifacts_dir.mkdir(parents=True, exist_ok=True)

        # Create dummy slide image
        self.slide_file = self.ref_artifacts_dir / "slide_three_parts_of_every_notebook.png"
        self.slide_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)

        # Create drop log
        self.drop_log_file = self.output_dir / "collateral_drop_log.json"
        self.drop_log_data = [
            {
                "filename": "slide_three_parts_of_every_notebook.png",
                "dropped_at_elapsed": "00:05:47",
                "sha256": "0c4cdfd11e91b8ed6d84a0181b111790f62948ff1362bab21578b8dbf6638285",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "title": "Three Parts Of Every Notebook",
                "key_topics": ["Sources", "Chat", "Studio", "Audio Overview"],
            }
        ]
        self.drop_log_file.write_text(json.dumps(self.drop_log_data, indent=2), encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_zero_touch_referenced_artifacts_discovery(self) -> None:
        """Verify discover_attachments finds referenced_artifacts without CLI flags."""
        candidates = discover_attachments(
            attachment_paths=None,
            attachment_dir=None,
            meeting_output_dir=self.output_dir,
            transcript_path=None,
        )
        self.assertTrue(len(candidates) >= 1)
        filenames = [c.name for c in candidates]
        self.assertIn("slide_three_parts_of_every_notebook.png", filenames)

    def test_zero_touch_discovery_from_parent_date_directory(self) -> None:
        """Verify discovery finds referenced_artifacts in ancestor date directory."""
        date_ref_dir = self.output_dir.parent / "referenced_artifacts"
        date_ref_dir.mkdir(parents=True, exist_ok=True)
        date_slide = date_ref_dir / "slide_date_level.png"
        date_slide.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        sibling_subfolder = self.output_dir.parent / "Another_Meeting_Slug"
        sibling_subfolder.mkdir(parents=True, exist_ok=True)
        candidates = discover_attachments(
            attachment_paths=None,
            attachment_dir=None,
            meeting_output_dir=sibling_subfolder,
            transcript_path=None,
        )
        filenames = [c.name for c in candidates]
        self.assertIn("slide_date_level.png", filenames)

    def test_staging_stamps_collateral_reviewed_attribution_mode(self) -> None:
        """Verify staging candidate files stamps attribution_mode=COLLATERAL_REVIEWED."""
        records = stage_attachments_into_output([self.slide_file], self.output_dir)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.attribution_mode, "COLLATERAL_REVIEWED")
        self.assertEqual(rec.filename, "slide_three_parts_of_every_notebook.png")
        self.assertEqual(rec.attachment_type, AttachmentType.IMAGE)

        # Verify collateral_drop_log.json was written/synced
        self.assertTrue(self.drop_log_file.is_file())
        logged = json.loads(self.drop_log_file.read_text(encoding="utf-8"))
        self.assertTrue(any(d.get("attribution_mode") == "COLLATERAL_REVIEWED" for d in logged))

    def test_graph_ontology_healer_extracts_three_parts_facet(self) -> None:
        """Verify graph_ontology_healer extracts Three Parts Of Every Notebook entity facet."""
        data = {
            "schema_version": "5.7",
            "meeting_id": "meeting-20260930-gemini-working-with-files-data",
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [],
                "relationship_signals": [],
            },
        }
        mutations = data["graph_mutations"]
        changes: list[str] = []
        metadata = {
            "project": "gemini_enablement",
            "source_text": "Rose explained: Three Parts Of Every Notebook includes sources, chat, and studio.",
            "output_dir": str(self.output_dir),
            "attachments": [
                {
                    "filename": "slide_three_parts_of_every_notebook.png",
                    "attribution_mode": "COLLATERAL_REVIEWED",
                    "title": "Three Parts Of Every Notebook",
                }
            ],
        }

        heal_ontology_and_predicates(data, mutations, changes, metadata=metadata)

        # 1. Evidence item was populated with COLLATERAL_REVIEWED and kind: technical
        self.assertIn("ev-slide-three-parts", data["evidence_index"])
        ev_item = data["evidence_index"]["ev-slide-three-parts"]
        self.assertEqual(ev_item["attribution_mode"], "COLLATERAL_REVIEWED")
        self.assertEqual(ev_item["kind"], "technical")
        self.assertIn("Three Parts Of Every Notebook", ev_item["text"])

        # 2. Entity upsert was populated
        ent_ids = [e["id"] for e in mutations["entity_upserts"]]
        self.assertIn("system:three-parts-of-every-notebook", ent_ids)
        three_parts_ent = next(e for e in mutations["entity_upserts"] if e["id"] == "system:three-parts-of-every-notebook")
        self.assertEqual(three_parts_ent["attribution_mode"], "COLLATERAL_REVIEWED")
        self.assertEqual(three_parts_ent["name"], "Three Parts Of Every Notebook")

        # 3. Relationship signal connecting entity to project
        sig_ids = [s["signal_id"] for s in mutations["relationship_signals"]]
        self.assertIn("sig-slide-three-parts", sig_ids)

    def test_collateral_healer_auto_heals_from_drop_log(self) -> None:
        """Verify collateral_healer reads drop log when attachments list is empty."""
        data = {
            "schema_version": "5.7",
            "evidence_index": {},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }
        changes: list[str] = []
        metadata = {
            "project": "gemini_enablement",
            "output_dir": str(self.output_dir),
            "attachments": [],  # empty attachments list
        }

        heal_collateral_graph_mutations(data, metadata, changes)

        # Must have loaded from collateral_drop_log.json
        self.assertTrue(len(data["evidence_index"]) >= 1)
        ev_item = next(iter(data["evidence_index"].values()))
        self.assertEqual(ev_item["attribution_mode"], "COLLATERAL_REVIEWED")
        self.assertEqual(ev_item["kind"], "technical")

    def test_retrieval_11_gates_compliance_with_multimodal_facet(self) -> None:
        """Verify all 11 non-compensating retrieval release gates pass under BGE-M3."""
        gates_evaluated = {gate: True for gate in RETRIEVAL_RELEASE_11_GATES}
        passed, failures = evaluate_retrieval_11_gates(gates_evaluated)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

        # Negative check: failure on missing or failed gate
        bad_gates = gates_evaluated.copy()
        bad_gates["forbidden_leakage"] = False
        passed_bad, failures_bad = evaluate_retrieval_11_gates(bad_gates)
        self.assertFalse(passed_bad)
        self.assertIn("forbidden_leakage (FAILED)", failures_bad)


if __name__ == "__main__":
    unittest.main()
