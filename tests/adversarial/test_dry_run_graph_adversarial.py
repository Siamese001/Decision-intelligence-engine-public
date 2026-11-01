"""
tests/adversarial/test_dry_run_graph_adversarial.py

Adversarial and edge-case testing for Wave 4 Graph Dry-Run and Promotion subsystem:
  1. Corrupted / malformed Output 03 JSON string.
  2. Empty Output 03 payload.
  3. Prohibited non-creatable node types triggering schema violations.
  4. Non-existent graph database path handled gracefully.
  5. High-volume entity and assertion batches respecting preview truncation limits.
  6. Unicode, emojis, and quotes in entity names and assertion claims.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_preview_core.preview import generate_graph_delta_preview


class TestDryRunGraphAdversarial(unittest.TestCase):
    """Adversarial and edge-case test suite for graph preview and dry-run mode."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_adversarial_malformed_json_output3(self) -> None:
        """Verify malformed JSON input string does not crash preview generation."""
        malformed = "{'invalid': json, truncated..."
        preview, preview_md = generate_graph_delta_preview(
            output_3=malformed,
            graph_path=None,
            output_dir=self.tmp_path,
        )
        self.assertEqual(preview["counts"]["nodes_added"], 0)
        self.assertEqual(preview["counts"]["assertions_new"], 0)
        self.assertIn("# Graph Delta Preview (Dry-Run Mode)", preview_md)

    def test_adversarial_empty_payload(self) -> None:
        """Verify empty dictionary produces zero-count delta without errors."""
        preview, _ = generate_graph_delta_preview(
            output_3={},
            graph_path=None,
            output_dir=self.tmp_path,
        )
        self.assertEqual(preview["counts"]["nodes_added"], 0)
        self.assertEqual(preview["counts"]["nodes_modified"], 0)
        self.assertEqual(preview["counts"]["assertions_new"], 0)
        self.assertEqual(preview["counts"]["relationships_new"], 0)
        self.assertTrue(preview["schema_conformance"]["valid"])

    def test_adversarial_prohibited_node_types_flagged(self) -> None:
        """Verify prohibited node types (card, source, source_pack) trigger schema violations."""
        adversarial_payload = {
            "entity_upserts": [
                {"entity_id": "card:exploit-01", "name": "Fake Card", "entity_type": "card"},
                {"entity_id": "source:injected-source", "name": "Fake Source", "entity_type": "source"},
                {"entity_id": "source_pack:pack-01", "name": "Fake Pack", "entity_type": "source_pack"},
                {"entity_id": "person:valid-person", "name": "Valid Person", "entity_type": "person"},
            ],
        }

        preview, preview_md = generate_graph_delta_preview(
            output_3=adversarial_payload,
            graph_path=None,
            output_dir=self.tmp_path,
        )

        violations = preview["schema_conformance"]["violations"]
        self.assertFalse(preview["schema_conformance"]["valid"])
        self.assertEqual(len(violations), 3)
        self.assertTrue(any("card" in v for v in violations))
        self.assertTrue(any("source" in v for v in violations))
        self.assertTrue(any("source_pack" in v for v in violations))
        self.assertIn("VIOLATIONS_DETECTED", preview_md)

    def test_adversarial_nonexistent_graph_path(self) -> None:
        """Verify non-existent graph database path is handled safely without exceptions."""
        nonexistent = self.tmp_path / "does_not_exist.sqlite"
        payload = {
            "entity_upserts": [{"entity_id": "person:bob", "name": "Bob", "entity_type": "person"}],
        }
        preview, _ = generate_graph_delta_preview(
            output_3=payload,
            graph_path=nonexistent,
            output_dir=self.tmp_path,
        )
        self.assertEqual(preview["counts"]["nodes_added"], 1)

    def test_adversarial_high_volume_batch_truncation(self) -> None:
        """Verify preview markdown truncates display at 25 items for large batches."""
        large_entities = [
            {"entity_id": f"person:user_{i}", "name": f"User {i}", "entity_type": "person"}
            for i in range(100)
        ]
        large_payload = {"entity_upserts": large_entities}

        preview, preview_md = generate_graph_delta_preview(
            output_3=large_payload,
            graph_path=None,
            output_dir=self.tmp_path,
        )
        self.assertEqual(preview["counts"]["nodes_added"], 100)
        # Markdown preview table should indicate truncation
        self.assertIn("and 75 more nodes", preview_md)

    def test_adversarial_unicode_and_special_characters(self) -> None:
        """Verify Unicode, quotes, and emoji in claims do not break JSON or Markdown preview."""
        payload = {
            "entity_upserts": [
                {"entity_id": "person:rené-müller", "name": "René Müller 🚀", "entity_type": "person"},
            ],
            "action_updates": [
                {
                    "action_id": "action:special-chars",
                    "description": "Evaluate 'strategic quote' with \"double quotes\" & <xml> tags.",
                    "status": "planned",
                },
            ],
        }
        preview, preview_md = generate_graph_delta_preview(
            output_3=payload,
            graph_path=None,
            output_dir=self.tmp_path,
        )
        self.assertEqual(preview["counts"]["nodes_added"], 1)
        self.assertEqual(preview["counts"]["assertions_new"], 1)
        self.assertIn("René Müller 🚀", preview_md)
        self.assertIn("Evaluate 'strategic quote'", preview_md)


if __name__ == "__main__":
    unittest.main()
