#!/usr/bin/env python3
"""
tests/adversarial/test_speaker_role_capture_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 1 Speaker Role Resolution Capture.
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload
2. Malformed input
3. Invariant bypass attempt
4. Concurrency & race
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import (
    C05TitleCompletenessGate,
    apply_hitl_role_resolution,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


@pytest.mark.adversarial
class TestSpeakerRoleCaptureAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case validation for speaker role resolution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_path = self.work_dir / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.json_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # --- CATEGORY 1: Boundary payload ---
    def test_boundary_empty_and_null_inputs(self) -> None:
        """Boundary: Empty string, whitespace-only, and null names/roles fail closed."""
        with self.assertRaises(ValueError):
            create_speaker_role_resolution(
                person_name="",
                resolved_role="Tech Lead",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )

        with self.assertRaises(ValueError):
            create_speaker_role_resolution(
                person_name="   ",
                resolved_role="Tech Lead",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )

        with self.assertRaises(ValueError):
            create_speaker_role_resolution(
                person_name="John Doe",
                resolved_role="",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )

        # Empty roster context clears without errors or mutations
        gate = C05TitleCompletenessGate(fail_closed=True, repo_root=self.work_dir)
        cleared_ctx = gate.execute({"metadata": {"roster": ""}})
        self.assertTrue(cleared_ctx["c05_title_gate_cleared"])

    # --- CATEGORY 2: Malformed input ---
    def test_malformed_unbalanced_rosters_and_org_conflation(self) -> None:
        """Malformed input: unbalanced brackets, non-standard roster items, and org conflations."""
        meta = {
            "roster": "Alice (PartnerAdvisory (Lead Architect, Bob [Unclosed, Charlie",
            "attendees": "Alice, Bob, Charlie",
        }
        # Apply resolution to Alice with complex unbalanced roster text
        receipt = apply_hitl_role_resolution(
            meta,
            "Alice",
            "Managing Director",
            store=self.store,
            repo_root=self.work_dir,
            is_human_confirmed=True,
        )
        self.assertEqual(receipt["status"], "RESOLVED_HITL")
        self.assertEqual(meta["hitl_role_resolutions"]["Alice"], "Managing Director")

        # Conflated org name masquerading as title is rejected
        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Bob",
                resolved_role="APEX",
                organization="Apex Global Enterprises",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("Conflation error", str(ctx.exception))

    # --- CATEGORY 3: Invariant bypass attempt ---
    def test_invariant_bypass_attempts_rejected(self) -> None:
        """Invariant bypass: synthetic system markers and false human claims fail closed."""
        # 1. Synthetic stop hook string
        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Eve",
                resolved_role="Security Engineer",
                provenance="HUMAN_OPERATOR",
                is_human_confirmed=True,
                operator_origin="stop hook blocked termination: automatically approved the artifact",
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("Synthetic approval rejected", str(ctx.exception))

        # 2. Forged is_human_confirmed=True with non-human provenance
        with self.assertRaises(ValueError) as ctx:
            create_speaker_role_resolution(
                person_name="Eve",
                resolved_role="Security Engineer",
                provenance="ENTERPRISE_DIRECTORY",
                is_human_confirmed=True,
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
        self.assertIn("Provenance integrity violation", str(ctx.exception))

        # 3. Forged metadata boolean without real receipts fails to clear ungrounded attendee
        gate = C05TitleCompletenessGate(fail_closed=True, repo_root=self.work_dir)
        bogus_context = {
            "metadata": {
                "attendee_roles_confirmed_by_operator": True,  # fake flag
                "roster": "Mallory",
                "pre_enrichment_roles": {"Mallory": ""},  # empty role
            }
        }
        with self.assertRaises(HITLRequiredError):
            gate.execute(bogus_context)

    # --- CATEGORY 4: Concurrency & race ---
    def test_concurrent_parallel_resolutions_ledger_safety(self) -> None:
        """Concurrency: 8 parallel workers resolving distinct attendees without lockups or corrupt digests."""
        attendees = [f"Attendee_{i}" for i in range(16)]
        roles = [f"Role_Specialist_{i}" for i in range(16)]

        def _resolve_worker(idx: int) -> dict:
            name = attendees[idx]
            role = roles[idx]
            res = create_speaker_role_resolution(
                person_name=name,
                resolved_role=role,
                is_human_confirmed=True,
                operator_origin={"source": "human_operator", "text": f"Approved {name}"},
                sqlite_path=self.sqlite_path,
                json_path=self.json_path,
            )
            return res.to_dict()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(_resolve_worker, range(16)))

        self.assertEqual(len(results), 16)
        # Reload and verify ledger consistency
        reloaded_store = HITLDecisionStore(self.json_path)
        history = reloaded_store.get_history()
        self.assertEqual(len(history), 16)


if __name__ == "__main__":
    unittest.main()
