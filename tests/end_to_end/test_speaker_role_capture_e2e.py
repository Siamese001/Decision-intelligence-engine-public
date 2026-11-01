#!/usr/bin/env python3
"""
tests/end_to_end/test_speaker_role_capture_e2e.py

End-to-End Test Suite for Wave 1 Speaker Role Resolution Capture Lifecycle.
Tests that authentic resolutions survive process restarts, ledger reloads, and clear C0.5.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


@pytest.mark.e2e
class TestSpeakerRoleCaptureE2E(unittest.TestCase):
    """E2E lifecycle testing for authentic speaker role capture, persistence, and gate clearance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.ledger_path = self.work_dir / "hitl_decision_ledger.json"
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.store = HITLDecisionStore(self.ledger_path)

        # Ensure isolated directory lookup fixture
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            _CANONICAL_ENTERPRISE_DIRECTORY,
        )
        self._orig_dir = dict(_CANONICAL_ENTERPRISE_DIRECTORY)
        for key in ("kevin mcknight", "kevin", "sarah connor", "sarah"):
            _CANONICAL_ENTERPRISE_DIRECTORY.pop(key, None)

    def tearDown(self) -> None:
        from dual_pipeline.transcript_metadata_core.entity_resolver import (
            _CANONICAL_ENTERPRISE_DIRECTORY,
        )
        _CANONICAL_ENTERPRISE_DIRECTORY.clear()
        _CANONICAL_ENTERPRISE_DIRECTORY.update(self._orig_dir)
        self.temp_dir.cleanup()

    def test_e2e_capture_survives_restart_and_clears_gate(self) -> None:
        """Full flow: missing role -> HITL interception -> authentic capture -> restart reload -> C0.5 cleared."""
        gate = C05TitleCompletenessGate(fail_closed=True, repo_root=self.work_dir)

        # 1. Initial meeting context with missing/placeholder title
        l0_context = {
            "metadata": {
                "meeting_name": "Cloud_Migration_Kickoff",
                "date": "10/08/2026",
                "project": "nea",
                "attendees": "Kevin McKnight (PartnerAdvisory), Sarah Connor",
                "roster": [
                    {"name": "Kevin McKnight", "role": "", "org": "PartnerAdvisory"},
                    {"name": "Sarah Connor", "role": "Project Manager", "org": "PartnerAdvisory"},
                ],
            }
        }

        # 2. Gate execution must halt on ungrounded Kevin McKnight
        with self.assertRaises(HITLRequiredError) as ctx:
            gate.execute(l0_context)

        err = ctx.exception
        self.assertIn("role:Kevin McKnight", err.missing_fields)

        # 3. Authentic human operator resolution
        receipt = apply_hitl_role_resolution(
            l0_context["metadata"],
            name="Kevin McKnight",
            resolved_role="Principal Cloud Architect",
            store=self.store,
            repo_root=self.work_dir,
            is_human_confirmed=True,
            provenance="HUMAN_OPERATOR",
            operator_origin={"source": "human_operator", "text": "Confirmed Principal Cloud Architect"},
        )
        self.assertEqual(receipt["status"], "RESOLVED_HITL")
        self.assertTrue(receipt["hitl_receipt_bound"])
        self.assertIsNotNone(receipt["ledger_entry_id"])

        # 4. Gate execution now passes in current session
        res_context = gate.execute(l0_context)
        self.assertTrue(res_context["c05_title_gate_cleared"])
        self.assertEqual(
            res_context["metadata"]["hitl_role_resolutions"]["Kevin McKnight"],
            "Principal Cloud Architect",
        )

        # 5. SIMULATED RESTART: Fresh process / new gate instance reading from persisted ledger
        reloaded_store = HITLDecisionStore(self.ledger_path)
        decisions = [
            d for d in reloaded_store.get_history()
            if d.get("subject") == "Kevin McKnight" or d.get("decision_id") == receipt.get("resolution_id")
        ]
        self.assertTrue(len(decisions) >= 1)
        persisted_choice = (
            decisions[-1].get("choice")
            or decisions[-1].get("selected_key")
            or decisions[-1].get("resolved_role")
        )
        self.assertEqual(persisted_choice, "Principal Cloud Architect")

        # 6. Re-run on fresh meeting run with reloaded metadata
        fresh_context = {
            "metadata": {
                "meeting_name": "Cloud_Migration_Followup",
                "date": "10/09/2026",
                "project": "nea",
                "attendees": "Kevin McKnight (PartnerAdvisory), Sarah Connor",
                "roster": [
                    {"name": "Kevin McKnight", "role": "", "org": "PartnerAdvisory"},
                    {"name": "Sarah Connor", "role": "Project Manager", "org": "PartnerAdvisory"},
                ],
                # Loaded from persistent store
                "hitl_role_resolutions": {
                    "Kevin McKnight": persisted_choice,
                },
            }
        }
        fresh_gate = C05TitleCompletenessGate(fail_closed=True, repo_root=self.work_dir)
        fresh_res = fresh_gate.execute(fresh_context)
        self.assertTrue(fresh_res["c05_title_gate_cleared"])


if __name__ == "__main__":
    unittest.main()
