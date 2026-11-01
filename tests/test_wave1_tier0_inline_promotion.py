"""Unit tests for Wave 1: Tier-0 Source-Bound Inline Canonical Promotion Unblocking."""

from __future__ import annotations

import unittest
from typing import Any

from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.strategic_reasoning_core.profiling import _profile


class Wave1Tier0InlinePromotionTests(unittest.TestCase):
    """Verify inline canonical promotion unblocking, calibrated gates, and fail-closed safety."""

    def _create_bundle(
        self,
        candidates: list[dict[str, Any]],
        *,
        inline_promo: bool = True,
        authority_rank: int = 7,
        strategic_profiles: dict[str, Any] | None = None,
        status_profiles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ext: dict[str, Any] = {
            "com.partner_advisory.nee.context": {
                "authority_rank": authority_rank,
                "inline_canonical_promotion": inline_promo,
                "source_kind": "evidence_envelope",
            },
            "com.partner_advisory.nee.inline-canonical-promotion": inline_promo,
        }
        if strategic_profiles:
            ext["com.partner_advisory.nee.strategic-candidate-profiles"] = strategic_profiles
        if status_profiles:
            ext["com.partner_advisory.nee.project-status-candidate-profiles"] = status_profiles

        return {
            "run_id": "run-" + "a" * 28,
            "trace_id": "0" * 32,
            "source_id": "source-test-w1",
            "candidates": candidates,
            "extensions": ext,
        }

    def _routes(self, candidates: list[dict[str, Any]], route_override: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "subject_id": c["candidate_id"],
                "intake_route": route_override or c.get("route", "NEW_ASSERTION"),
                "reason_codes": [],
            }
            for c in candidates
        ]

    def test_clean_factual_with_strategic_profile_promotes_inline(self) -> None:
        cand = {
            "candidate_id": "c-strat-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.72,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        strat_p = _profile(
            "current_state_fact", "observed", "nee_current_state", "nee_specific", "observed"
        )
        bundle = self._create_bundle([cand], strategic_profiles={"c-strat-01": strat_p})
        routes = self._routes([cand])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(len(disps), 1)
        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")
        self.assertEqual(disps[0]["persistent_lane"], "approved")

    def test_clean_status_snapshot_promotes_inline(self) -> None:
        cand = {
            "candidate_id": "c-status-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.75,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand], status_profiles={"c-status-01": {"status": "on_track"}})
        routes = self._routes([cand])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")

    def test_corroborating_evidence_promotes_inline_without_prior_decision(self) -> None:
        cand = {
            "candidate_id": "c-corroborate-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.85,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand], route_override="CORROBORATING_EVIDENCE")
        disps = build_dispositions(bundle, routes, [], decisions=())

        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")
        self.assertIn("inline canonical promotion for corroborating evidence", disps[0]["reason_codes"])

    def test_action_and_relationship_promote_inline(self) -> None:
        act = {
            "candidate_id": "c-act-01",
            "candidate_type": "action",
            "assertion_type": "action_item",
            "extraction_confidence": 0.85,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
            "route": "ACTION_ITEM",
        }
        rel = {
            "candidate_id": "c-rel-01",
            "candidate_type": "relationship",
            "assertion_type": "architecture_relationship",
            "extraction_confidence": 0.88,
            "evidence_ids": ["ev-2"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
            "route": "NEW_RELATIONSHIP",
        }
        bundle = self._create_bundle([act, rel])
        routes = self._routes([act, rel])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")
        self.assertEqual(disps[1]["disposition"], "APPROVED")
        self.assertEqual(disps[1]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")

    def test_factual_confidence_boundary_70_vs_69(self) -> None:
        c_70 = {
            "candidate_id": "c-fact-70",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.70,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        c_69 = {
            "candidate_id": "c-fact-69",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.69,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([c_70, c_69])
        routes = self._routes([c_70, c_69])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")
        self.assertEqual(disps[1]["disposition"], "CANDIDATE")
        self.assertEqual(disps[1]["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")

    def test_speculative_confidence_boundary_80_vs_79(self) -> None:
        c_80 = {
            "candidate_id": "c-spec-80",
            "candidate_type": "assertion",
            "assertion_type": "hypothesis",
            "extraction_confidence": 0.80,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        c_79 = {
            "candidate_id": "c-spec-79",
            "candidate_type": "assertion",
            "assertion_type": "hypothesis",
            "extraction_confidence": 0.79,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([c_80, c_79])
        routes = self._routes([c_80, c_79])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "APPROVED")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_APPROVED_WRITE")
        self.assertEqual(disps[1]["disposition"], "CANDIDATE")
        self.assertEqual(disps[1]["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")

    def test_fail_closed_contradiction_quarantined(self) -> None:
        cand = {
            "candidate_id": "c-contra-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "HIGH",
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand], route_override="CONTRADICTION")
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")
        self.assertEqual(disps[0]["persistent_lane"], "none")

    def test_fail_closed_open_question_quarantined(self) -> None:
        cand = {
            "candidate_id": "c-oq-01",
            "candidate_type": "open_question",
            "assertion_type": "question",
            "extraction_confidence": 0.95,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "LOW",
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand], route_override="QUARANTINE")
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")

    def test_fail_closed_missing_evidence_rejected_noop(self) -> None:
        cand = {
            "candidate_id": "c-no-ev-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": [],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "REJECTED_NOOP")
        self.assertEqual(disps[0]["x3_outcome"], "X3R_REJECT_NOOP")

    def test_fail_closed_unresolved_identity_quarantined(self) -> None:
        cand = {
            "candidate_id": "c-unresolved-id",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [{"mention_id": "m-unresolved"}],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand])
        resolutions = [{"mention_id": "m-unresolved", "outcome": "HITL_CONFIRM_ENTITY", "requires_hitl": True}]
        disps = build_dispositions(bundle, routes, resolutions)

        self.assertEqual(disps[0]["disposition"], "QUARANTINE")
        self.assertEqual(disps[0]["x3_outcome"], "X3Q_QUARANTINE")

    def test_explicit_force_hitl_stays_candidate(self) -> None:
        cand = {
            "candidate_id": "c-forced-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
            "force_hitl": True,
        }
        bundle = self._create_bundle([cand])
        routes = self._routes([cand])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "CANDIDATE")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")

    def test_legacy_staging_mode_without_inline_promotion(self) -> None:
        cand = {
            "candidate_id": "c-legacy-01",
            "candidate_type": "assertion",
            "assertion_type": "candidate_assertion",
            "extraction_confidence": 0.95,
            "evidence_ids": ["ev-1"],
            "entity_mentions": [],
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "materiality": "MEDIUM",
        }
        strat_p = _profile(
            "current_state_fact", "observed", "nee_current_state", "nee_specific", "observed"
        )
        bundle = self._create_bundle(
            [cand],
            inline_promo=False,
            strategic_profiles={"c-legacy-01": strat_p},
        )
        routes = self._routes([cand])
        disps = build_dispositions(bundle, routes, [])

        self.assertEqual(disps[0]["disposition"], "CANDIDATE")
        self.assertEqual(disps[0]["x3_outcome"], "X3D_ALLOW_CANDIDATE_STAGE")


if __name__ == "__main__":
    unittest.main()
