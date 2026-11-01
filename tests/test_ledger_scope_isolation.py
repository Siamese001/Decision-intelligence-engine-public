"""Unit tests for decision ledger scope isolation, preventing cross-meeting
speaker cluster leakage.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from dual_pipeline.speaker_attribution_core.audio_uncertainty_detector import (
    check_ledger_for_audio_resolution,
)


class LedgerScopeIsolationTests(unittest.TestCase):
    """Test suite for meeting-scoped decision ledger lookups."""

    def setUp(self) -> None:
        self.mock_store = MagicMock()
        # Simulated legacy ledger record: Daniel Brankin resolved as Speaker 1 in Old_Nexus_Sync
        self.mock_records = [
            {
                "decision_id": "speaker_attribution_speaker_1_2026-08-15",
                "category": "speaker_attribution",
                "selected_option": "Daniel Brankin (Delivery Lead)",
                "metadata": {
                    "cluster": "Speaker 1",
                    "project": "project_nexus",
                    "meeting_name": "Old_Nexus_Sync",
                    "resolved_identity": "Daniel Brankin",
                },
            }
        ]
        self.mock_store._load_records.return_value = self.mock_records

    def test_prevent_cross_project_leakage(self) -> None:
        """Daniel Brankin in project_nexus must never resolve in partner_alliances."""
        res = check_ledger_for_audio_resolution(
            cluster_id="Speaker 1",
            store=self.mock_store,
            project="partner_alliances",
            meeting_name="PartnerAdvisory_GCP_Alex_Amit",
        )
        self.assertIsNone(res, "Cross-project decision ledger leak detected!")

    def test_prevent_cross_meeting_generic_cluster_leakage_same_project(self) -> None:
        """Generic 'Speaker 1' must never leak to a different meeting even within the same project."""
        res = check_ledger_for_audio_resolution(
            cluster_id="Speaker 1",
            store=self.mock_store,
            project="project_nexus",
            meeting_name="PartnerAdvisory_GCP_Alex_Amit",
        )
        self.assertIsNone(res, "Cross-meeting generic cluster leak detected!")

    def test_resolves_when_meeting_scope_matches_exactly(self) -> None:
        """When meeting_name and project match the ledger record, resolution succeeds."""
        res = check_ledger_for_audio_resolution(
            cluster_id="Speaker 1",
            store=self.mock_store,
            project="project_nexus",
            meeting_name="Old_Nexus_Sync",
        )
        self.assertEqual(res, "Daniel Brankin")


if __name__ == "__main__":
    unittest.main()
