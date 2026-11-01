"""tests/test_u0_entity_normalization.py

Continuous Graph Expansion & Ingestion Front-Door Tests:
1. Stage U0 raw transcript speaker header normalization (e.g. **Jarrod Bryant:** -> **Jarod Bryant:**).
2. Attendee roster metadata normalization and company realignment (e.g. Jack O'Shea -> Apex Global Analytics).
3. GraphDB stakeholder write gate preventing splinter node creation (e.g. Jarrod Bryant -> person_jarod_bryant).
4. CI Directory Invariants static check passage (zero duplicate canonical entities).
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    sync_stakeholder_to_graph,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary
from tools.lint_entity_directory_invariants import lint_directory_invariants


class TestU0EntityNormalization(unittest.TestCase):
    def setUp(self):
        self.u0 = U0IngestionBoundary()

    def test_u0_speaker_header_rewriting(self):
        """Verify misspelled speaker headers are rewritten to canonical names in U0 clean_transcript."""
        raw_text = (
            "# Sprint Review\n\n"
            "**Jarrod Bryant:** Welcome everyone to the quarterly review.\n"
            "**Alexandra Corrow:** Happy to present the operational technology updates.\n"
            "**Jen Pan:** Thanks Jarod and Alexandra.\n"
        )
        metadata = {
            "project": "project_lens",
            "meeting_name": "Sprint_Review",
            "date": "09/24/2026",
            "time": "04:00 PM - 04:30 PM ET",
            "classification": "confidential",
            "attendees": ["Jarrod Bryant", "Alexandra Corrow", "Jen Pan"],
            "attendee_roles_confirmed_by_operator": True,
            "bypass_mandatory_attendee_confirmation": True,
        }

        result = self.u0.execute(raw_text, metadata)
        clean_text = result["clean_transcript"]

        # Assert speaker headers were rewritten
        self.assertIn("**Jarod Bryant:**", clean_text)
        self.assertNotIn("**Jarrod Bryant:**", clean_text)
        self.assertIn("**Alexandra Coro:**", clean_text)
        self.assertNotIn("**Alexandra Corrow:**", clean_text)

        # Assert normalizations logged in metadata
        norms = result["metadata"].get("entity_normalizations", [])
        raw_speakers = [n["raw_speaker"] for n in norms]
        self.assertIn("Jarrod Bryant", raw_speakers)
        self.assertIn("Alexandra Corrow", raw_speakers)

    def test_u0_roster_company_realignment(self):
        """Verify attendee list with wrong company parenthetical is sanitized to canonical org."""
        raw_text = "**Jack O'Shea:** Checking in.\n"
        metadata = {
            "project": "project_lens",
            "meeting_name": "Sprint_Review",
            "date": "09/24/2026",
            "time": "04:00 PM - 04:30 PM ET",
            "classification": "confidential",
            "attendees": {
                "PartnerAdvisory": ["Jack O'Shea (PartnerAdvisory)"],
            },
            "attendee_roles_confirmed_by_operator": True,
            "bypass_mandatory_attendee_confirmation": True,
        }

        result = self.u0.execute(raw_text, metadata)
        attendees = result["metadata"]["attendees"]

        # Jack O'Shea should be under Apex Global Analytics or Apex Global Enterprises, NOT PartnerAdvisory
        partner_advisory_list = attendees.get("PartnerAdvisory", [])
        self.assertFalse(any("Jack O'Shea" in s for s in partner_advisory_list), "Jack O'Shea should not be grouped under PartnerAdvisory")

    def test_graphdb_stakeholder_write_gate(self):
        """Verify GraphDB write gate intercepts raw typos and forces canonical node ID and name."""
        with tempfile.NamedTemporaryFile(suffix=".sqlite") as tmp:
            tmp_db = Path(tmp.name)
            conn = sqlite3.connect(tmp_db)
            conn.execute(
                "CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, display_name TEXT, description TEXT, status TEXT, active INTEGER, created_at TEXT, updated_at TEXT)"
            )
            conn.execute(
                "CREATE TABLE alias (alias_name TEXT PRIMARY KEY, canonical_name TEXT, active INTEGER)"
            )
            conn.commit()
            conn.close()

            # Attempt to insert misspelled "Jarrod Bryant"
            success = sync_stakeholder_to_graph(
                person_name="Jarrod Bryant",
                role="Executive Sponsor",
                org="Apex Global Enterprises (PGD)",
                db_path=tmp_db,
            )
            self.assertTrue(success)

            # Query node table: should have canonical name and canonical node_id
            conn = sqlite3.connect(tmp_db)
            cur = conn.cursor()
            cur.execute("SELECT node_id, canonical_name FROM node")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 1)
            node_id, c_name = rows[0]
            self.assertEqual(c_name, "Jarod Bryant")
            self.assertEqual(node_id, "person_jarod_bryant")

            # Query alias table: should have "Jarrod Bryant" registered
            cur.execute("SELECT alias_name, canonical_name FROM alias")
            aliases = cur.fetchall()
            self.assertIn(("Jarrod Bryant", "Jarod Bryant"), aliases)
            conn.close()

    def test_directory_invariant_linter(self):
        """Verify CI directory invariant linter passes with zero violations."""
        rc = lint_directory_invariants(ROOT)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
