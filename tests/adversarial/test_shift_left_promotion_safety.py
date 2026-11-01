"""Adversarial security and fail-closed safety tests for Shift-Left Promotion."""

from __future__ import annotations

import unittest

from dual_pipeline.contract_validation.issues import ValidationFailure
from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.hitl import _decision_map, create_shift_left_hitl_decision


class ShiftLeftPromotionSafetyAdversarialTests(unittest.TestCase):
    def _create_bundle(self, candidates: list[dict], extensions: dict | None = None) -> dict:
        ext = extensions or {
            "com.partner_advisory.nee.inline-canonical-promotion": True,
            "com.partner_advisory.nee.authority-rank": 7,
        }
        return {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "run_id": "run-adv-001",
            "trace_id": "a" * 32,
            "source_id": "source-adv-001",
            "candidates": candidates,
            "extensions": ext,
        }

    def test_spoofed_inline_promotion_with_low_authority_rank_fails_closed(self) -> None:
        """Low authority rank (< 7) must not promote claims inline to APPROVED."""
        cand = {
            "candidate_id": "cand-low-auth",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.85,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        # Spoofed inline-canonical-promotion with insufficient authority rank (rank = 3)
        bundle = self._create_bundle([cand], extensions={
            "com.partner_advisory.nee.inline-canonical-promotion": True,
            "com.partner_advisory.nee.authority-rank": 3,
        })
        routes = [{"subject_id": "cand-low-auth", "intake_route": "NEW_ASSERTION", "reason_codes": []}]
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(len(disps), 1)
        # Fails closed to CANDIDATE
        self.assertEqual(disps[0]["disposition"], "CANDIDATE")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")
        self.assertEqual(disps[0]["persistent_lane"], "candidate")
        self.assertIn("source authority rank is insufficient", disps[0]["reason_codes"][0])

    def test_missing_evidence_ids_fails_closed_to_rejected_noop(self) -> None:
        """Claims with missing evidence IDs must fail closed to REJECTED_NOOP."""
        cand_no_ev = {
            "candidate_id": "cand-no-ev",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": [],  # Missing evidence
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "HIGH",
        }
        bundle = self._create_bundle([cand_no_ev])
        routes = [{"subject_id": "cand-no-ev", "intake_route": "NEW_ASSERTION", "reason_codes": []}]
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(len(disps), 1)
        self.assertEqual(disps[0]["disposition"], "REJECTED_NOOP")
        self.assertEqual(disps[0]["x3_outcome"], "X3R_REJECT_NOOP")
        self.assertEqual(disps[0]["persistent_lane"], "none")

    def test_unresolved_contradiction_without_decision_fails_closed_to_quarantine(self) -> None:
        """Contradictions without authentic operator decision must be quarantined."""
        cand_contra = {
            "candidate_id": "cand-contra-adv",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.90,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "HIGH",
        }
        bundle = self._create_bundle([cand_contra])
        routes = [{"subject_id": "cand-contra-adv", "intake_route": "CONTRADICTION", "reason_codes": []}]
        disps = build_dispositions(bundle, routes, [], decisions=[])

        self.assertEqual(len(disps), 1)
        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")
        self.assertEqual(disps[0]["persistent_lane"], "none")

    def test_a2a_agent_forged_decision_fails_closed(self) -> None:
        """Forged decision artifacts or non-HITL decision contracts must raise errors."""
        forged_decision = {
            "contract_id": "urn:nee:dual-pipeline:synthetic-auto-decision:1.0.0",
            "decision_id": "fake-dec-01",
            "affected_candidate_ids": ["cand-01"],
            "decision": "PROMOTE_APPROVED",
        }
        with self.assertRaises(Exception):
            _decision_map([forged_decision])

    def test_unresolved_identity_fails_closed_to_quarantine(self) -> None:
        """Unresolved entity mentions must fail closed to QUARANTINE."""
        cand_unresolved = {
            "candidate_id": "cand-unresolved-id",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.90,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [{"mention_id": "mention-ghost"}],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand_unresolved])
        routes = [{"subject_id": "cand-unresolved-id", "intake_route": "NEW_ASSERTION", "reason_codes": []}]
        resolutions = [{
            "mention_id": "mention-ghost",
            "outcome": "UNRESOLVED",
            "requires_hitl": True,
        }]
        disps = build_dispositions(bundle, routes, resolutions)

        self.assertEqual(len(disps), 1)
        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")

    def test_open_question_fails_closed_to_quarantine(self) -> None:
        """Open question candidate types must fail closed to QUARANTINE."""
        cand_oq = {
            "candidate_id": "cand-open-q",
            "candidate_type": "open_question",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.85,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand_oq])
        routes = [{"subject_id": "cand-open-q", "intake_route": "NEW_ASSERTION", "reason_codes": []}]
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(len(disps), 1)
        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")


if __name__ == "__main__":
    unittest.main()
