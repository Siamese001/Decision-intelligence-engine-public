"""Unit and integration tests for GS-10 Temporal Freshness Decay & Calibrated Confidence Scoring."""
from __future__ import annotations

import unittest

from dual_pipeline.graph_retrieval_core.scoring import (
    _better_path,
    _edge_increment,
    _parse_date_epoch,
    _path,
)


class GraphTemporalScoringTests(unittest.TestCase):
    def test_parse_date_epoch(self) -> None:
        self.assertIsNone(_parse_date_epoch(None))
        self.assertIsNone(_parse_date_epoch(12345))
        self.assertIsNone(_parse_date_epoch("not-a-date"))

        ep = _parse_date_epoch("2026-09-25T12:00:00Z")
        self.assertIsNotNone(ep)
        self.assertGreater(ep, 1.7e9)

    def test_temporal_freshness_decay_scoring(self) -> None:
        one_day_ago = "2026-09-24T00:00:00Z"
        ninety_days_ago = "2026-06-26T00:00:00Z"

        edge_fresh = {
            "relationship_family": "DELIVERS",
            "evidence_ids": ["ev_1"],
            "materiality": "high",
            "confidence_score": 0.90,
            "recorded_at": one_day_ago,
        }
        edge_stale = {
            "relationship_family": "DELIVERS",
            "evidence_ids": ["ev_1"],
            "materiality": "high",
            "confidence_score": 0.90,
            "recorded_at": ninety_days_ago,
        }

        # Calculate increments with reference_epoch fixed to now_ep
        # Convert ninety_days_ago to approx timestamp relative to now_ep
        ref_fresh = _parse_date_epoch(one_day_ago) + 86400.0  # 1 day after one_day_ago
        inc_fresh, topo_fresh = _edge_increment(
            edge_fresh,
            requested_relationships={"DELIVERS"},
            destination_degree=3,
            hop=1,
            reference_epoch=ref_fresh,
        )

        ref_stale = _parse_date_epoch(ninety_days_ago) + 90 * 86400.0  # 90 days after ninety_days_ago
        inc_stale, topo_stale = _edge_increment(
            edge_stale,
            requested_relationships={"DELIVERS"},
            destination_degree=3,
            hop=1,
            reference_epoch=ref_stale,
        )

        # Fresh edge MUST score strictly higher than stale edge
        self.assertGreater(inc_fresh, inc_stale)
        self.assertGreater(topo_fresh, topo_stale)
        diff = inc_fresh - inc_stale
        self.assertGreaterEqual(diff, 0.04)  # ~4-6% boost delta

    def test_confidence_calibration_scoring(self) -> None:
        edge_high_conf = {
            "relationship_family": "OWNS_OR_LEADS",
            "evidence_ids": [],
            "materiality": "medium",
            "confidence_score": 0.95,
            "recorded_at": None,
        }
        edge_low_conf = {
            "relationship_family": "OWNS_OR_LEADS",
            "evidence_ids": [],
            "materiality": "medium",
            "confidence_score": 0.20,
            "recorded_at": None,
        }

        inc_high, topo_high = _edge_increment(
            edge_high_conf,
            requested_relationships={"OWNS_OR_LEADS"},
            destination_degree=2,
            hop=1,
        )
        inc_low, topo_low = _edge_increment(
            edge_low_conf,
            requested_relationships={"OWNS_OR_LEADS"},
            destination_degree=2,
            hop=1,
        )

        self.assertGreater(inc_high, inc_low)
        self.assertGreater(topo_high, topo_low)

    def test_better_path_selection_prefers_higher_score(self) -> None:
        path_a = _path(
            seed_record_id="rec_seed",
            target_record_id="rec_target_fresh",
            target_assertion_id="asrt_fresh",
            lane="approved",
            origin="test",
            attachment_kind="direct",
            path_score=0.85,
            topology_score=0.75,
            steps=[{"hop": 1, "assertion_id": "asrt_fresh", "from_node_id": "n1", "to_node_id": "n2", "evidence_ids": []}],
        )
        path_b = _path(
            seed_record_id="rec_seed",
            target_record_id="rec_target_stale",
            target_assertion_id="asrt_stale",
            lane="approved",
            origin="test",
            attachment_kind="direct",
            path_score=0.65,
            topology_score=0.55,
            steps=[{"hop": 1, "assertion_id": "asrt_stale", "from_node_id": "n1", "to_node_id": "n2", "evidence_ids": []}],
        )

        # Candidate path_a with higher score is better than path_b
        self.assertTrue(_better_path(path_a, path_b))
        # Candidate path_b with lower score is not better than path_a
        self.assertFalse(_better_path(path_b, path_a))


if __name__ == "__main__":
    unittest.main()
