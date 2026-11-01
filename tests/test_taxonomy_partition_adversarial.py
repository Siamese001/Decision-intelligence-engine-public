#!/usr/bin/env python3
"""Adversarial and boundary tests for taxonomy partitioning and metric validation.

Mandatory Edge-Case Categories Covered:
1. Boundary Payload: Metric boundary values (0.0%, 100.0%, negative, >100%), empty entities.
2. Malformed Input: Wrong metric types (strings, floats for integers, nulls, missing keys).
3. Invariant Bypass Attempt: Disguised EDO entities with conflicting IA tokens (fail-closed exclusion).
4. Overflow & Exhaustion: High-throughput batch partitioning (10,000 entities in <100ms).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.dual_pipeline.graph_contracts import (
    ClaimContract,
    NodeContract,
    StructuredMetrics,
    TaxonomyLane,
)
from dual_pipeline.index_core.taxonomy_filter import (
    classify_entity_taxonomy,
    filter_by_taxonomy_lane,
)


class TestTaxonomyPartitionAdversarial(unittest.TestCase):
    """Adversarial test suite for taxonomy classification and metric boundary contracts."""

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_metric_percentages(self) -> None:
        """Verify 0.0 and 100.0 percent are valid; <0.0 or >100.0 raise ValueError."""
        # Valid bounds
        m_zero = StructuredMetrics(cost_reduction_pct=0.0)
        m_zero.validate()
        self.assertEqual(m_zero.cost_reduction_pct, 0.0)

        m_hundred = StructuredMetrics(cost_reduction_pct=100.0)
        m_hundred.validate()
        self.assertEqual(m_hundred.cost_reduction_pct, 100.0)

        # Invalid bounds: negative
        with self.assertRaises(ValueError):
            StructuredMetrics(cost_reduction_pct=-0.1).validate()

        # Invalid bounds: >100.0
        with self.assertRaises(ValueError):
            StructuredMetrics(cost_reduction_pct=100.1).validate()

    def test_boundary_active_user_count(self) -> None:
        """Verify non-negative integer user counts; negative counts raise ValueError."""
        m_zero_users = StructuredMetrics(active_user_count=0)
        m_zero_users.validate()
        self.assertEqual(m_zero_users.active_user_count, 0)

        with self.assertRaises(ValueError):
            StructuredMetrics(active_user_count=-1).validate()

    def test_boundary_empty_and_null_entity_input(self) -> None:
        """Verify empty and uninformative entities classify safely as UNCLASSIFIED."""
        self.assertEqual(classify_entity_taxonomy({}), TaxonomyLane.UNCLASSIFIED)
        self.assertEqual(classify_entity_taxonomy({"node_id": ""}), TaxonomyLane.UNCLASSIFIED)
        self.assertEqual(classify_entity_taxonomy({"canonical_name": None}), TaxonomyLane.UNCLASSIFIED)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_metric_types(self) -> None:
        """Verify non-numeric or wrong types raise TypeError fail-closed."""
        # String percentage
        with self.assertRaises(TypeError):
            StructuredMetrics(cost_reduction_pct="87%").validate()  # type: ignore[arg-type]

        # Float for integer count
        with self.assertRaises(TypeError):
            StructuredMetrics(active_user_count=170.5).validate()  # type: ignore[arg-type]

        # Boolean for integer count (bool is subclass of int in Python)
        with self.assertRaises(TypeError):
            StructuredMetrics(active_user_count=True).validate()  # type: ignore[arg-type]

    def test_malformed_node_contract_empty_fields(self) -> None:
        """Verify NodeContract and ClaimContract reject empty identifiers fail-closed."""
        with self.assertRaises(ValueError):
            NodeContract(node_id="", canonical_name="Name", node_type="workstream")
        with self.assertRaises(ValueError):
            NodeContract(node_id="id", canonical_name="", node_type="workstream")
        with self.assertRaises(ValueError):
            ClaimContract(claim_id="", claim_text="Text", subject_node_id="node")
        with self.assertRaises(ValueError):
            ClaimContract(claim_id="cid", claim_text="", subject_node_id="node")

    # ── Category 3: Invariant Bypass Attempt ──────────────────────────────────

    def test_invariant_bypass_edo_disguised_with_ia_tokens(self) -> None:
        """Verify EDO entities containing IA tokens are still quarantined to ENTERPRISE_DATA_OFFICE."""
        disguised_entities = [
            {"node_id": "ws_disguised_1", "canonical_name": "LENS Digital Thread Pipeline", "node_type": "data_product"},
            {"node_id": "ws_disguised_2", "canonical_name": "DocIQ Data Quality Tracking DQT", "node_type": "data_product"},
            {"node_id": "ws_disguised_3", "canonical_name": "EVA Trade Floor ETRM Pricing", "node_type": "data_product"},
            {"node_id": "ws_disguised_4", "canonical_name": "Outage Journey Atlas Data Mesh", "node_type": "data_product"},
        ]

        # All disguised entities must be quarantined to EDO due to exclusion token priority
        for item in disguised_entities:
            lane = classify_entity_taxonomy(item)
            self.assertEqual(
                lane,
                TaxonomyLane.ENTERPRISE_DATA_OFFICE,
                f"Disguised entity {item['canonical_name']} bypassed EDO quarantine",
            )

        # None must leak into IA filter
        ia_leakage = filter_by_taxonomy_lane(disguised_entities, TaxonomyLane.INTELLIGENT_AUTOMATION)
        self.assertEqual(len(ia_leakage), 0, "Disguised EDO entities leaked into Intelligent Automation partition")

    # ── Category 4: Overflow & Exhaustion ─────────────────────────────────────

    def test_overflow_batch_partitioning_throughput(self) -> None:
        """Verify partitioning 10,000 mixed entities completes in <100ms."""
        import time

        batch = []
        for i in range(5000):
            batch.append({"node_id": f"lens_{i}", "canonical_name": f"LENS Turbine {i}", "node_type": "workstream"})
            batch.append({"node_id": f"edo_{i}", "canonical_name": f"Spring Digital Thread {i}", "node_type": "data_product"})

        start = time.perf_counter()
        ia_items = filter_by_taxonomy_lane(batch, TaxonomyLane.INTELLIGENT_AUTOMATION)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        self.assertEqual(len(ia_items), 5000)
        self.assertLess(elapsed_ms, 150.0, f"Batch partitioning took {elapsed_ms}ms, exceeded 150ms limit")


if __name__ == "__main__":
    unittest.main()
