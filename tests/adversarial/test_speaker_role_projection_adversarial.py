#!/usr/bin/env python3
"""
tests/adversarial/test_speaker_role_projection_adversarial.py

Adversarial test suite for Wave 2 speaker role projection and normalization.
Covers:
1. Boundary: Empty/null/whitespace inputs in resolutions and projections.
2. Invariant bypass & malformed input: Unsupported predicates and ambiguous identity rejection.
3. Foreign-run evidence isolation: Mismatched run_id rejection.
4. Stale answer digest detection: Tampered or desynchronized resolution digest rejection.
5. Conflicting facts detection: Irreconcilable role assignments for the same person.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    create_speaker_role_resolution,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
    validate_speaker_role_resolution_for_projection,
)


class TestSpeakerRoleProjectionAdversarial(unittest.TestCase):
    """Adversarial security and invariant preservation tests for speaker role projection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_path = self.work_dir / "hitl_decision_ledger.json"
        self.metadata = {
            "run_id": "run-adv-w2-001",
            "meeting_id": "meet-adv-w2-001",
            "title": "Adversarial Test Meeting",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_boundary_empty_null_and_sparse_inputs(self) -> None:
        """Boundary category: empty metadata, empty resolutions, and empty projection payloads."""
        # None metadata returns unmodified projection
        proj: Dict[str, Any] = {"graph_mutations": {"entity_upserts": []}}
        res_none = reconcile_speaker_role_resolutions(proj, None)
        self.assertEqual(res_none, proj)

        # Empty resolutions in metadata returns unmodified projection
        res_empty = reconcile_speaker_role_resolutions(proj, {"hitl_role_resolutions": {}})
        self.assertEqual(res_empty, proj)

        # Empty name raises ValueError
        with self.assertRaises(ValueError) as ctx:
            validate_speaker_role_resolution_for_projection(
                {"person_name": "", "resolved_role": "Architect"},
                self.metadata,
            )
        self.assertIn("Ambiguous identity rejected", str(ctx.exception))

        # Empty role raises ValueError
        with self.assertRaises(ValueError) as ctx:
            validate_speaker_role_resolution_for_projection(
                {"person_name": "Alice Smith", "resolved_role": ""},
                self.metadata,
            )
        self.assertIn("Empty or missing role", str(ctx.exception))

    def test_invariant_bypass_unsupported_predicates_rejected(self) -> None:
        """Invariant bypass category: unapproved graph predicates fail closed."""
        fake_res = {
            "person_name": "Bob Stone",
            "resolved_role": "Lead Architect",
            "predicate": "FABRICATED_UNAUTHORIZED_EDGE",
            "source_input": {"run_id": "run-adv-w2-001"},
        }
        with self.assertRaises(ValueError) as ctx:
            validate_speaker_role_resolution_for_projection(fake_res, self.metadata)
        self.assertIn("Unsupported predicate", str(ctx.exception))

    def test_ambiguous_identity_generic_placeholders_rejected(self) -> None:
        """Malformed input category: generic personas (Speaker, Attendee, Someone) fail closed."""
        for generic in ("Speaker", "Attendee", "Someone", "Participant", "Unknown", "User"):
            fake_res = {
                "person_name": generic,
                "resolved_role": "Lead Architect",
                "source_input": {"run_id": "run-adv-w2-001"},
            }
            with self.assertRaises(ValueError) as ctx:
                validate_speaker_role_resolution_for_projection(fake_res, self.metadata)
            self.assertIn("Ambiguous identity rejected", str(ctx.exception))

    def test_foreign_run_evidence_isolation(self) -> None:
        """Cross-tenant / foreign-run evidence boundary: mismatched run_id fails closed."""
        res = create_speaker_role_resolution(
            person_name="Carlos Mendez",
            resolved_role="DevOps Lead",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            source_input={"run_id": "foreign-run-999-other-meeting"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"Carlos Mendez": res}

        proj: Dict[str, Any] = {"graph_mutations": {"entity_upserts": []}}
        with self.assertRaises(ValueError) as ctx:
            reconcile_speaker_role_resolutions(proj, self.metadata)
        self.assertIn("Foreign-run evidence rejected", str(ctx.exception))

    def test_stale_answer_digest_tampering_rejected(self) -> None:
        """Tampered / stale answer digest detection fails closed."""
        res = create_speaker_role_resolution(
            person_name="Diana Prince",
            resolved_role="Security Director",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            source_input={"run_id": "run-adv-w2-001"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        # Intentionally tamper with the digest
        tampered_res = {
            "person_name": res.person_name,
            "resolved_role": res.resolved_role,
            "organization": res.organization,
            "provenance": res.provenance,
            "is_human_confirmed": res.is_human_confirmed,
            "source_input": res.source_input,
            "digest": "f" * 64,  # forged fake digest
        }
        with self.assertRaises(ValueError) as ctx:
            validate_speaker_role_resolution_for_projection(tampered_res, self.metadata)
        self.assertIn("Stale or corrupted answer digest", str(ctx.exception))

    def test_conflicting_facts_detection_fails_closed(self) -> None:
        """Contradiction category: two conflicting roles for the same person in one run fail closed."""
        res1 = create_speaker_role_resolution(
            person_name="Frank Miller",
            resolved_role="VP of Engineering",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            source_input={"run_id": "run-adv-w2-001"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        res2 = create_speaker_role_resolution(
            person_name="Frank Miller",
            resolved_role="Junior Intern",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            source_input={"run_id": "run-adv-w2-001"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {
            "res_1": res1,
            "res_2": res2,
        }

        proj: Dict[str, Any] = {"graph_mutations": {"entity_upserts": []}}
        with self.assertRaises(ValueError) as ctx:
            reconcile_speaker_role_resolutions(proj, self.metadata)
        self.assertIn("Conflicting facts detected", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
