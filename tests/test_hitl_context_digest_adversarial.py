"""
tests/test_hitl_context_digest_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 3:
Covers:
1. Boundary payloads (empty transcript, single-turn boundary cluster with 2-before/2-after unavailable).
2. Malformed input and invariant bypass (stale context_digest, tampered turn text, empty digest).
3. Interval suppression for uncalibrated priors (n=0 suppresses [lo%-hi%, n=0]).
4. Scoped prior isolation across (project, subject_key) boundaries.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dual_pipeline.hitl_governance_core.bayesian import (
    _accumulate_historical_weights,
    compute_bayesian_calibrated_score,
)
from dual_pipeline.hitl_governance_core.layman_clarity import (
    format_layman_display_label,
    should_render_credible_interval,
)
from dual_pipeline.speaker_attribution_core.review_context import (
    SpeakerReviewContextError,
    build_speaker_review_context,
)
from dual_pipeline.speaker_attribution_core.review_context_units import (
    build_grouped_review_contexts,
    grouped_context_digest,
)
from dual_pipeline.transcript_stages_core.cluster_attribution import (
    apply_cluster_attribution_resolution,
    build_cluster_review_items,
    parse_cluster_turns,
)


class TestHitlContextDigestAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for context windows, digest binding, and priors."""

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # --------------------------------------------------------------------------
    def test_single_turn_boundary_payload_unavailable_counts(self) -> None:
        """Single turn transcript at boundary correctly yields before=2 and after=2 unavailable."""
        transcript = "[00:00] Speaker 1: Lone turn in the entire meeting."
        turns = parse_cluster_turns(transcript, "source-single")
        self.assertEqual(len(turns), 1)

        contexts, status = build_grouped_review_contexts(turns, ["0"])
        self.assertEqual(status, "ready")
        self.assertEqual(len(contexts), 1)
        ctx = contexts[0]
        # At position 0 of a 1-turn transcript, both preceding and following are unavailable
        self.assertEqual(ctx["before_unavailable"], 2)
        self.assertEqual(ctx["after_unavailable"], 2)
        self.assertEqual(len(ctx["turns"]), 1)
        self.assertTrue(ctx["turns"][0]["is_target"])

    def test_empty_transcript_boundary_payload(self) -> None:
        """Empty transcript returns zero review items safely without crashing."""
        items, contexts, digest, prompt, options = build_cluster_review_items(
            "",
            ["Speaker 1"],
            metadata={"meeting_name": "Empty Meeting"},
        )
        self.assertEqual(len(items), 0)
        self.assertEqual(len(contexts), 0)
        self.assertEqual(digest, "0" * 64)

    # --------------------------------------------------------------------------
    # Category 2: Malformed Input & Invariant Bypass Attempts
    # --------------------------------------------------------------------------
    def test_stale_or_tampered_context_digest_rejected_fail_closed(self) -> None:
        """Forged or mismatched context_digest is rejected with SpeakerReviewContextError."""
        transcript = (
            "[00:01] Speaker 1: First point.\n"
            "[00:05] Bob: Second point.\n"
            "[00:10] Speaker 1: Third point."
        )
        items, contexts, digest, prompt, options = build_cluster_review_items(
            transcript,
            ["Speaker 1"],
            metadata={"project": "proj_x", "meeting_name": "Test"},
        )
        self.assertEqual(len(items), 1)
        item = items[0]

        # Scenario A: Forged digest with invalid hash
        with self.assertRaises(SpeakerReviewContextError):
            apply_cluster_attribution_resolution(
                transcript,
                item,
                resolved_speaker="Alice",
                context_digest="f" * 64,
            )

        # Scenario B: Empty digest string
        with self.assertRaises(SpeakerReviewContextError):
            apply_cluster_attribution_resolution(
                transcript,
                item,
                resolved_speaker="Alice",
                context_digest="",
            )

    def test_duplicate_turn_ids_reject_context_construction(self) -> None:
        """Duplicate turn IDs in source material violate context invariants and fail closed."""
        malformed_turns = [
            {"turn_id": "1", "source_position": 0, "speaker": "Speaker 1", "text": "Turn 1", "source_id": "src1"},
            {"turn_id": "1", "source_position": 1, "speaker": "Bob", "text": "Turn 1 duplicate", "source_id": "src1"},
        ]
        with self.assertRaises(SpeakerReviewContextError):
            build_speaker_review_context(malformed_turns, "1", "src1")

    # --------------------------------------------------------------------------
    # Category 3: Uncalibrated Prior Interval Suppression (n=0)
    # --------------------------------------------------------------------------
    def test_uncalibrated_prior_interval_suppression(self) -> None:
        """Sample count n=0 strictly suppresses bracketed credible interval [lo%-hi%, n=0]."""
        # 1. should_render_credible_interval returns False when n <= 0 or None
        self.assertFalse(should_render_credible_interval(n=0, lo=0.05, hi=0.95))
        self.assertFalse(should_render_credible_interval(n=None, lo=0.05, hi=0.95))
        self.assertFalse(should_render_credible_interval(n=-5, lo=0.05, hi=0.95))

        # 2. format_layman_display_label suppresses interval string when sample count is 0
        opt_uncalibrated = {
            "label": "Assign Alice to cluster Speaker 1",
            "confidence_score": 0.85,
            "confidence_pct": 85,
            "confidence_level": "HIGH",
            "sample_size": 0,
            "credible_interval": [0.05, 0.95],
            "recommended": True,
        }
        rendered = format_layman_display_label(opt_uncalibrated)
        self.assertNotIn("n=0", rendered)
        self.assertNotIn("[5%–95%", rendered)
        self.assertIn("(Recommended) Assign Alice to cluster Speaker 1 (Confidence: HIGH (85%))", rendered)

    def test_degenerate_interval_width_suppressed_regardless_of_sample_size(self) -> None:
        """Interval width >= 0.80 represents degenerate dispersion and must be suppressed."""
        self.assertFalse(should_render_credible_interval(n=100, lo=0.05, hi=0.95))
        self.assertFalse(should_render_credible_interval(n=50, lo=0.10, hi=0.92))

    # --------------------------------------------------------------------------
    # Category 4: Scoped Prior Isolation (project, subject_key)
    # --------------------------------------------------------------------------
    def test_scoped_prior_isolation_across_projects(self) -> None:
        """Priors learned in Project A do not leak into Project B decisions."""
        now = datetime.now(timezone.utc)
        history = [
            {
                "category": "SPEAKER_ATTRIBUTION",
                "selected_key": "Alice",
                "project": "project_alpha",
                "subject_key": "Speaker 1",
                "metadata": {"project": "project_alpha", "subject_key": "Speaker 1"},
                "authority_type": "HUMAN",
                "recorded_at": now.isoformat(),
            },
            {
                "category": "SPEAKER_ATTRIBUTION",
                "selected_key": "Bob",
                "project": "project_beta",
                "subject_key": "Speaker 1",
                "metadata": {"project": "project_beta", "subject_key": "Speaker 1"},
                "authority_type": "HUMAN",
                "recorded_at": now.isoformat(),
            },
        ]

        # When querying project_alpha, only Alice's weight is accumulated
        aff_w_a, _, aff_c_a, _ = _accumulate_historical_weights(
            history,
            category="SPEAKER_ATTRIBUTION",
            half_life_days=None,
            now=now,
            project="project_alpha",
            subject_key="Speaker 1",
        )
        self.assertIn("alice", aff_w_a)
        self.assertNotIn("bob", aff_w_a)
        self.assertEqual(aff_c_a["alice"], 1)

        # When querying project_beta, only Bob's weight is accumulated
        aff_w_b, _, aff_c_b, _ = _accumulate_historical_weights(
            history,
            category="SPEAKER_ATTRIBUTION",
            half_life_days=None,
            now=now,
            project="project_beta",
            subject_key="Speaker 1",
        )
        self.assertIn("bob", aff_w_b)
        self.assertNotIn("alice", aff_w_b)
        self.assertEqual(aff_c_b["bob"], 1)

    def test_scoped_prior_isolation_across_subject_keys(self) -> None:
        """Priors learned for Speaker 1 do not alter calibration for Speaker 2."""
        now = datetime.now(timezone.utc)
        history = [
            {
                "category": "SPEAKER_ATTRIBUTION",
                "selected_key": "Alice",
                "project": "project_alpha",
                "subject_key": "Speaker 1",
                "authority_type": "HUMAN",
                "recorded_at": now.isoformat(),
            },
        ]

        # Query for Speaker 2 in the same project -> zero accumulated weight
        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(
            history,
            category="SPEAKER_ATTRIBUTION",
            half_life_days=None,
            now=now,
            project="project_alpha",
            subject_key="Speaker 2",
        )
        self.assertEqual(len(aff_w), 0)
        self.assertEqual(len(aff_c), 0)


if __name__ == "__main__":
    unittest.main()
