"""
tests/test_derivation_integrity_and_healer_purity.py

Validates Wave 2 derivation integrity and healer purity.
Asserts:
1. table_healer does not inject hardcoded attendee lists or dates.
2. appendix_healer does not hallucinate fake dialogue for silent attendees.
3. graph_healer applies learned aliases without hardcoded vendor checks.
4. transcript_qa_core healers do not contain hardcoded attendee signatures or fake conversational dialogue.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.table_healer import heal_output_2_table


class TestDerivationIntegrityAndHealerPurity(unittest.TestCase):
    def test_table_healer_no_hardcoded_attendees_or_dates(self):
        sample_recap = (
            "# Executive Recap\n\n"
            "**Date:** [Pending Date]\n"
            "**Attendees:** PartnerAdvisory: None\n\n"
            "## Next Steps & Action Items\n"
            "Warning: Low action density\n"
            "| # | Action | Owner | Target |\n"
            "| :--- | :--- | :--- | :--- |\n"
        )
        changes = []
        healed = heal_output_2_table(sample_recap, changes)
        self.assertNotIn("Amit Ayer", healed)
        self.assertNotIn("Jake Stark", healed)
        self.assertNotIn("Miles Erickson", healed)
        self.assertNotIn("09/25/2026", healed)

    def test_appendix_healer_no_synthetic_dialogue_generation(self):
        sample_recap = (
            "# Executive Recap\n\n"
            "**Time:** 01:00 PM – 02:00 PM ET\n"
            "**Attendees:**\n"
            "- PartnerAdvisory: Silent Member (Consultant)\n\n"
            "## Strategic Insights\n"
            "Some high level discussions.\n"
        )
        sample_transcript = "Speaker 1: Let us begin the session on architecture."
        changes = []
        healed = heal_output_2_appendix(sample_recap, metadata={"duration_minutes": 60}, changes=changes, output_1=sample_transcript)
        # Asserts no fake dialogue was injected
        self.assertNotIn("clarified that", healed)
        self.assertNotIn("confirmed that", healed)
        self.assertNotIn("requested that", healed)
        self.assertNotIn("noted that", healed)

    def test_graph_healer_no_hardcoded_vendor_alias(self):
        projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:test_system",
                        "name": "Test System",
                        "type": "System",
                        "attributes": {},
                    },
                    {
                        "id": "system:gateway",
                        "name": "Gateway",
                        "type": "System",
                        "attributes": {},
                    },
                ],
                "relationship_signals": [
                    {
                        "from_id": "system:test_system",
                        "to_id": "system:gateway",
                        "relationship": "INTEGRATES_WITH",
                    }
                ],
            }
        }
        changes = []
        healed = heal_graph_projection(projection, changes)
        entities = healed["graph_mutations"].get("entity_upserts", [])
        self.assertTrue(len(entities) > 0)
        entity = entities[0]
        self.assertNotIn("vendor_alias", entity.get("attributes", {}))

    def test_healer_source_ast_clean_of_synthetic_names(self):
        repo_root = Path(__file__).resolve().parent.parent
        qa_core_dir = repo_root / "dual_pipeline" / "transcript_qa_core"
        healer_files = [
            qa_core_dir / "table_healer.py",
            qa_core_dir / "appendix_healer.py",
            qa_core_dir / "graph_healer.py",
        ]
        banned_strings = [
            "Amit Ayer",
            "Jake Stark",
            "Miles Erickson",
            "09/25/2026",
            "TechnoSilva",
        ]
        for h_file in healer_files:
            content = h_file.read_text(encoding="utf-8")
            for banned in banned_strings:
                self.assertNotIn(
                    banned,
                    content,
                    f"Forbidden hardcoded literal '{banned}' discovered in {h_file.name}",
                )


if __name__ == "__main__":
    unittest.main()
