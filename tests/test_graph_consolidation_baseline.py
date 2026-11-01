"""
tests/test_graph_consolidation_baseline.py

Unit and regression tests for Graph Consolidation Wave 1 baseline verification.
Validates that active pair metadata, residual duplication counts, and sealed baseline
receipt adhere strictly to empirical graph state.
"""
from __future__ import annotations

import json
import unittest

from tools.collect_graph_consolidation_baseline import (
    DEFAULT_RECEIPT_PATH,
    DEFAULT_RUNTIME_ROOT,
    canonical_json_digest,
    collect_baseline,
)


class TestGraphConsolidationBaseline(unittest.TestCase):
    """Verifies baseline empirical probes against active graph state."""

    def setUp(self) -> None:
        self.runtime_root = DEFAULT_RUNTIME_ROOT
        self.receipt_path = DEFAULT_RECEIPT_PATH

    def test_baseline_receipt_exists_and_digest_valid(self) -> None:
        """Baseline receipt exists, has schema 1.0.0, and has valid cryptographic digest."""
        self.assertTrue(
            self.receipt_path.exists(),
            f"Receipt not found at {self.receipt_path}",
        )
        data = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema_version"), "1.0.0")
        self.assertEqual(data.get("receipt_type"), "graph_consolidation_wave1_baseline")

        claimed_digest = data.get("receipt_digest")
        self.assertIsNotNone(claimed_digest)

        data_without_digest = dict(data)
        data_without_digest.pop("receipt_digest", None)
        computed_digest = canonical_json_digest(data_without_digest)
        self.assertEqual(claimed_digest, computed_digest)

    def test_active_pair_sha_and_zero_tier2_mutations(self) -> None:
        """Validates that active pair graph has 0 Tier 2 audit events and 0 merged nodes."""
        data = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        probes = data.get("empirical_probes", {})

        # Zero Tier 2 mutations in active graph
        self.assertEqual(probes.get("tier2_audit_events_count"), 0)
        self.assertEqual(probes.get("nodes_status_merged_count"), 0)

        # All 10 hardcoded target entities are present and active
        targets = probes.get("hardcoded_tier2_targets", [])
        self.assertEqual(len(targets), 10)
        for target in targets:
            self.assertEqual(target.get("active"), 1)

    def test_residual_duplicate_counts(self) -> None:
        """Validates that residual duplicate counts reflect existing active backlog."""
        data = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        probes = data.get("empirical_probes", {})

        # Exact name duplicate groups
        self.assertGreaterEqual(probes.get("same_type_exact_name_dup_groups_count", 0), 4)
        self.assertGreaterEqual(probes.get("cross_type_exact_name_dup_groups_count", 0), 7)

        # Exact triple duplicate groups
        self.assertGreaterEqual(probes.get("exact_triple_dup_groups_count", 0), 40)
        summary = probes.get("exact_triple_dup_summary", {})
        self.assertGreaterEqual(summary.get("groups_confidence_differs", 0), 30)
        self.assertGreaterEqual(summary.get("groups_multi_source", 0), 30)

        # Lineage and predicate synonyms are clean (0 unmigrated, 0 deprecated active)
        self.assertEqual(probes.get("unmigrated_live_edges_count"), 0)
        self.assertEqual(probes.get("deprecated_predicates_count"), 0)

    def test_collector_is_reproducible_and_query_only(self) -> None:
        """Re-running collect_baseline against active graph yields identical probes."""
        run1 = collect_baseline(self.runtime_root)
        run2 = collect_baseline(self.runtime_root)

        self.assertEqual(
            run1["active_pair"]["graph_sha256"],
            run2["active_pair"]["graph_sha256"],
        )
        self.assertEqual(
            run1["empirical_probes"]["tier2_audit_events_count"],
            run2["empirical_probes"]["tier2_audit_events_count"],
        )
        self.assertEqual(
            run1["empirical_probes"]["exact_triple_dup_groups_count"],
            run2["empirical_probes"]["exact_triple_dup_groups_count"],
        )


if __name__ == "__main__":
    unittest.main()
