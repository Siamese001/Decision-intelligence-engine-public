"""Unit tests for Wave 2: HITL Decision Ledger Schema Enrichment (1.1.0)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core import HITLDecisionStore


class TestHITLLedgerEnrichment(unittest.TestCase):
    """Verifies schema version 1.1.0, outcome_type classification, margin telemetry, and migration."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_hitl_enrich_"))
        self.ledger_path = self.temp_dir / "hitl_decision_ledger.json"
        self.sqlite_path = self.temp_dir / "hitl_decision_ledger.sqlite"
        self.store = HITLDecisionStore(ledger_path=self.ledger_path, sqlite_path=self.sqlite_path, use_sqlite=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_decision_populates_enriched_telemetry(self) -> None:
        """record_decision derives outcome_type, margin_at_decision, and credible interval."""
        options = [
            {"key": "opt_a", "label": "Recommended Option", "confidence": 0.85, "recommended": True},
            {"key": "opt_b", "label": "Alternative Option", "confidence": 0.65},
        ]

        # Case 1: Affirmation (selected matches recommended)
        rec_aff = self.store.record_decision(
            decision_id="dec_test_001",
            category="entity_resolution",
            prompt="Select entity resolution strategy",
            options=options,
            selected_option=options[0],
            operator_id="human_operator",
        )

        self.assertEqual(rec_aff["outcome_type"], "AFFIRMED")
        self.assertAlmostEqual(rec_aff["margin_at_decision"], 0.20, places=2)
        self.assertIn("confidence_interval_at_decision", rec_aff)
        self.assertEqual(len(rec_aff["confidence_interval_at_decision"]), 2)
        self.assertIn("sample_count_at_presentation", rec_aff)
        self.assertIn("entry_digest", rec_aff)

        # Case 2: Override (selected differs from recommended)
        rec_ovr = self.store.record_decision(
            decision_id="dec_test_002",
            category="entity_resolution",
            prompt="Select entity resolution strategy",
            options=options,
            selected_option=options[1],
            operator_id="human_operator",
        )

        self.assertEqual(rec_ovr["outcome_type"], "OVERRIDDEN")
        self.assertAlmostEqual(rec_ovr["margin_at_decision"], 0.20, places=2)

        # Case 3: Autonomous engine execution
        rec_auto = self.store.record_decision(
            decision_id="dec_test_003",
            category="entity_resolution",
            prompt="Auto resolved",
            options=options,
            selected_option=options[0],
            operator_id="autonomous_engine",
        )

        self.assertEqual(rec_auto["outcome_type"], "AUTONOMOUS")

    def test_schema_version_is_1_1_0(self) -> None:
        """Saved ledger JSON file must declare schema_version 1.1.0."""
        self.store.record_decision(
            decision_id="dec_schema_test",
            category="testing",
            prompt="Prompt",
            options=["A", "B"],
            selected_option="A",
        )

        raw = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "1.1.0")
        self.assertEqual(raw["record_count"], 1)
        self.assertIn("ledger_digest", raw)

    def test_enrich_legacy_records_migration_helper(self) -> None:
        """enrich_legacy_records backfills outcome_type and margin on v1.0.0 records and reseals Merkle chain."""
        # Create a mock 1.0.0 ledger on disk
        legacy_records = [
            {
                "entry_id": "legacy_rec_1",
                "sequence_number": 0,
                "decision_id": "dec_legacy_1",
                "category": "role_assignment",
                "operator_id": "human_operator",
                "prompt": "Assign role",
                "options": [
                    {"key": "engineer", "label": "Engineer", "confidence": "80%"},
                    {"key": "analyst", "label": "Analyst", "confidence": "60%"},
                ],
                "selected_option": {"key": "engineer", "label": "Engineer"},
                "selected_key": "engineer",
                "rationale": "Affirmed recommendation",
                "previous_entry_hash": "0" * 64,
                "recorded_at": "2026-09-01T12:00:00Z",
                "metadata": {},
                "entry_digest": "dummy_digest_1",
            },
            {
                "entry_id": "legacy_rec_2",
                "sequence_number": 1,
                "decision_id": "dec_legacy_2",
                "category": "role_assignment",
                "operator_id": "human_operator",
                "prompt": "Assign role",
                "options": [
                    {"key": "lead", "label": "Lead", "confidence": "75%"},
                    {"key": "contributor", "label": "Contributor", "confidence": "55%"},
                ],
                "selected_option": {"key": "contributor", "label": "Contributor"},
                "selected_key": "contributor",
                "rationale": "Overrode recommendation",
                "previous_entry_hash": "dummy_digest_1",
                "recorded_at": "2026-09-01T12:10:00Z",
                "metadata": {},
                "entry_digest": "dummy_digest_2",
            },
        ]

        from dual_pipeline.core import digest, write_json
        raw_envelope = {
            "schema_version": "1.0.0",
            "record_count": 2,
            "updated_at": "2026-09-01T12:15:00Z",
            "genesis_hash": "0" * 64,
            "latest_entry_hash": "dummy_digest_2",
            "decisions": legacy_records,
        }
        raw_envelope["ledger_digest"] = digest(raw_envelope)
        write_json(self.ledger_path, raw_envelope)

        # Clear cache and run enrichment
        HITLDecisionStore._RECORDS_CACHE.clear()
        enriched_count = self.store.enrich_legacy_records()
        self.assertEqual(enriched_count, 2)

        # Load records and verify
        records = self.store._load_records(verify_integrity=True)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["outcome_type"], "AFFIRMED")
        self.assertAlmostEqual(records[0]["margin_at_decision"], 0.20, places=2)
        self.assertEqual(records[1]["outcome_type"], "OVERRIDDEN")
        self.assertAlmostEqual(records[1]["margin_at_decision"], 0.20, places=2)

        # Verify Merkle chain is unbroken
        self.assertEqual(records[1]["previous_entry_hash"], records[0]["entry_digest"])


if __name__ == "__main__":
    unittest.main()
