#!/usr/bin/env python3
"""End-to-end integration test for taxonomy partitioning and structured metric extraction.

Wave 3 Deliverable:
- Verifies filtering on taxonomy_lane == 'INTELLIGENT_AUTOMATION' selects 15 IA deliverables and 0 EDO workstreams.
- Verifies structured metrics (cost_reduction_pct, active_user_count, etc.) validate against contracts.
- Verifies zero leakage of Enterprise Data Office data products into client-facing automation scopes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.dual_pipeline.graph_contracts import (
    ClaimContract,
    NodeContract,
    StructuredMetrics,
    TaxonomyLane,
)
from dual_pipeline.core import read_json
from dual_pipeline.index_core.taxonomy_filter import (
    classify_entity_taxonomy,
    filter_by_taxonomy_lane,
    partition_graph_nodes,
)


@pytest.mark.e2e
class TestTaxonomyPartitionE2E(unittest.TestCase):
    """E2E test suite for taxonomy lane isolation and structured claim metrics."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return
        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.graph_path = runtime_root / cls.active_pair["graph_path"]

    def test_e2e_partition_15_ia_workstreams_zero_edo_leakage(self) -> None:
        """Verify the 15 Intelligent Automation deliverables are classified as IA with 0 EDO leakage."""
        mixed_portfolio = [
            # 15 Core Intelligent Automation Deliverables
            {"node_id": "ws_lens", "canonical_name": "LENS – Visual Asset Intelligence System", "node_type": "workstream"},
            {"node_id": "ws_dociq", "canonical_name": "DocIQ – Enterprise Document Intelligence & Extraction", "node_type": "workstream"},
            {"node_id": "ws_nucleus", "canonical_name": "Nuclear Engineering Change & Scheduling (Nucleus / Nexus Planning)", "node_type": "workstream"},
            {"node_id": "ws_eva", "canonical_name": "EVA – Electric Vehicle Customer-Care Copilot", "node_type": "workstream"},
            {"node_id": "ws_outage", "canonical_name": "Outage Communications Application (Outage Journey)", "node_type": "workstream"},
            {"node_id": "ws_spade", "canonical_name": "SPADE / APEX SSUP – Substation Sizing & Design Automation", "node_type": "workstream"},
            {"node_id": "ws_regai", "canonical_name": "RegAI & FlexAI – Regulatory Compliance & Intelligence Platform", "node_type": "workstream"},
            {"node_id": "ws_fcdb", "canonical_name": "Fault Code Database (FCDB) / ROCC Conduit", "node_type": "workstream"},
            {"node_id": "ws_pd_conduit", "canonical_name": "PD Conduit / Conductor (Arc Angel) – P&C Field Assistant", "node_type": "workstream"},
            {"node_id": "ws_sherpa", "canonical_name": "Project Beacon – Architecture Multi-Agent Platform", "node_type": "workstream"},
            {"node_id": "ws_devhub", "canonical_name": "DevHub Foundry – Developer Enablement Platform", "node_type": "workstream"},
            {"node_id": "ws_bille", "canonical_name": "Bill-E (High Bill Chatbot) – Customer Experience", "node_type": "workstream"},
            {"node_id": "ws_pgd_copilot", "canonical_name": "PGD CoPilot & Bedrock Data POC", "node_type": "workstream"},
            {"node_id": "ws_swarm", "canonical_name": "Swarm – Storm Response & Grid Preparedness", "node_type": "workstream"},
            {"node_id": "ws_gemini", "canonical_name": "Enterprise Gemini Enablement – Transformation", "node_type": "workstream"},

            # Enterprise Data Office (EDO) Data Products to be excluded
            {"node_id": "ws_digital_thread", "canonical_name": "Digital Thread Data Product", "node_type": "data_product"},
            {"node_id": "ws_dqt", "canonical_name": "DQT Data Quality Tracking", "node_type": "data_product"},
            {"node_id": "ws_gridopti", "canonical_name": "GridOpti Transmission Optimization", "node_type": "data_product"},
            {"node_id": "ws_spring", "canonical_name": "Project Spring Finance Data Lake", "node_type": "data_product"},
            {"node_id": "ws_trade_floor", "canonical_name": "Trade Floor ETRM Pricing Workbook", "node_type": "data_product"},
            {"node_id": "ws_atlas", "canonical_name": "Atlas Enterprise Data Mesh", "node_type": "data_product"},
        ]

        ia_filtered = filter_by_taxonomy_lane(mixed_portfolio, TaxonomyLane.INTELLIGENT_AUTOMATION)
        edo_filtered = filter_by_taxonomy_lane(mixed_portfolio, TaxonomyLane.ENTERPRISE_DATA_OFFICE)

        # Invariant 1: Exactly 15 IA workstreams selected
        self.assertEqual(len(ia_filtered), 15, "Expected exactly 15 Intelligent Automation deliverables")

        # Invariant 2: Exactly 6 EDO workstreams isolated
        self.assertEqual(len(edo_filtered), 6, "Expected exactly 6 EDO data products")

        # Invariant 3: Zero leakage of EDO into IA partition
        ia_names = {item["canonical_name"] for item in ia_filtered}
        for edo_item in edo_filtered:
            self.assertNotIn(
                edo_item["canonical_name"],
                ia_names,
                f"EDO workstream {edo_item['canonical_name']} leaked into Intelligent Automation partition",
            )

    def test_e2e_structured_metric_extraction_and_contracts(self) -> None:
        """Verify structured metrics on claims validate against schema bounds and serialize cleanly."""
        metrics_dociq = StructuredMetrics(
            cost_reduction_pct=87.0,
            duration_seconds=12.5,
        )
        metrics_eva = StructuredMetrics(
            active_user_count=170,
        )
        metrics_lens = StructuredMetrics(
            nte_budget=421000.0,
        )
        metrics_outage = StructuredMetrics(
            inbound_reduction_pct=20.0,
            active_user_count=1000,
        )

        claim_dociq = ClaimContract(
            claim_id="claim-dociq-cost",
            claim_text="Optimized document processing cost from $0.95 to $0.12 (~87% reduction)",
            subject_node_id="ws_dociq",
            taxonomy_lane=TaxonomyLane.INTELLIGENT_AUTOMATION,
            structured_metrics=metrics_dociq,
        )
        claim_eva = ClaimContract(
            claim_id="claim-eva-users",
            claim_text="Expanded conversational assistant to 170+ active users",
            subject_node_id="ws_eva",
            taxonomy_lane=TaxonomyLane.INTELLIGENT_AUTOMATION,
            structured_metrics=metrics_eva,
        )
        claim_lens = ClaimContract(
            claim_id="claim-lens-sow",
            claim_text="SOW-backed delivery under $421k NTE budget",
            subject_node_id="ws_lens",
            taxonomy_lane=TaxonomyLane.INTELLIGENT_AUTOMATION,
            structured_metrics=metrics_lens,
        )

        # Validate serialized payload
        dociq_data = claim_dociq.structured_metrics.to_dict()
        self.assertEqual(dociq_data["cost_reduction_pct"], 87.0)
        self.assertEqual(dociq_data["duration_seconds"], 12.5)

        eva_data = claim_eva.structured_metrics.to_dict()
        self.assertEqual(eva_data["active_user_count"], 170)

        lens_data = claim_lens.structured_metrics.to_dict()
        self.assertEqual(lens_data["nte_budget"], 421000.0)

        outage_data = metrics_outage.to_dict()
        self.assertEqual(outage_data["inbound_reduction_pct"], 20.0)
        self.assertEqual(outage_data["active_user_count"], 1000)


if __name__ == "__main__":
    unittest.main()
