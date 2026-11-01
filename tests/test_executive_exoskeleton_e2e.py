"""End-to-end integration tests for Executive Cognitive Exoskeleton & GraphDB Elevation.

Validates:
1. Migration idempotency (v56 schema & analytical views).
2. Semantic Spine V2 expansion (conflicts_with, duplicates).
3. 5 Golden CTO Queries across realistic enterprise scenarios.
4. Executive analytics engine synthesis and financialized waste estimation.
5. CLI execution via `nee executive-insights`.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest

from dual_pipeline.core import ROOT
from dual_pipeline.executive_analytics import ExecutiveAnalyticsEngine
from dual_pipeline.semantic_spine_v2 import SemanticSpineV2Error, validate_relation


def _create_baseline_schema(conn: sqlite3.Connection) -> None:
    """Create minimal v1-v55 baseline tables required for joins."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS node (
        node_id TEXT PRIMARY KEY,
        node_type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        display_name TEXT,
        description TEXT,
        status TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS edge (
        edge_id TEXT PRIMARY KEY,
        from_node_id TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        to_node_id TEXT NOT NULL,
        description TEXT,
        evidence_posture TEXT NOT NULL,
        confidence TEXT NOT NULL,
        materiality TEXT NOT NULL,
        validation_priority TEXT,
        validation_owner TEXT,
        hitl_required INTEGER DEFAULT 0,
        source_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        superseded_by TEXT,
        FOREIGN KEY (from_node_id) REFERENCES node(node_id),
        FOREIGN KEY (to_node_id) REFERENCES node(node_id)
    );

    CREATE TABLE IF NOT EXISTS assertion (
        assertion_id TEXT PRIMARY KEY,
        subject_node_id TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object_node_id TEXT,
        literal_value TEXT,
        knowledge_role TEXT,
        applicability_scope TEXT,
        created_at TEXT NOT NULL
    );
    """)


class ExecutiveExoskeletonE2ETests(unittest.TestCase):
    """Full lifecycle testing for the Executive Cognitive Exoskeleton."""

    def setUp(self) -> None:
        self.db_path = ROOT / ".local" / "test_executive_exoskeleton.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()

        self.conn = sqlite3.connect(str(self.db_path))
        _create_baseline_schema(self.conn)

        # Apply v56 migration
        v56_path = ROOT / "graph" / "migrations" / "v56_executive_strategic_exoskeleton.sql"
        self.assertTrue(v56_path.exists(), f"Missing migration file: {v56_path}")
        self.conn.executescript(v56_path.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.conn.close()
        if self.db_path.exists():
            self.db_path.unlink()

    def test_01_migration_idempotency(self) -> None:
        """Applying v56 migration twice must succeed with zero errors."""
        v56_path = ROOT / "graph" / "migrations" / "v56_executive_strategic_exoskeleton.sql"
        # Second application
        self.conn.executescript(v56_path.read_text(encoding="utf-8"))

        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        views = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }

        expected_tables = {
            "business_capability",
            "architectural_decision",
            "technical_debt_item",
            "vendor_contract_dependency",
            "stakeholder_political_stance",
            "inter_initiative_edge",
        }
        expected_views = {
            "v56_cross_silo_contradictions",
            "v56_duplicative_capability_spend",
            "v56_hidden_decision_blockers",
            "v56_key_person_fragility",
            "v56_reusable_ip_assets",
        }

        self.assertTrue(expected_tables.issubset(tables))
        self.assertTrue(expected_views.issubset(views))

    def test_02_semantic_spine_v2_validation(self) -> None:
        """Validate that conflicts_with and duplicates are strictly enforced under W13."""
        # Valid conflicts_with
        cw_valid = validate_relation(
            relation="conflicts_with",
            source_type="initiative",
            target_type="initiative",
            qualifiers={
                "conflict_type": "ARCHITECTURAL_STANDARDS",
                "source_assertion_ids": ["as-init-01", "as-init-02"],
            },
        )
        self.assertEqual(cw_valid["relation"], "conflicts_with")
        self.assertEqual(cw_valid["qualifiers"]["conflict_type"], "ARCHITECTURAL_STANDARDS")

        # Invalid conflicts_with (missing qualifiers)
        with self.assertRaises(SemanticSpineV2Error):
            validate_relation(
                relation="conflicts_with",
                source_type="initiative",
                target_type="initiative",
                qualifiers={"source_assertion_ids": ["as-01"]},  # missing conflict_type
            )

        # Valid duplicates
        dup_valid = validate_relation(
            relation="duplicates",
            source_type="capability",
            target_type="capability",
            qualifiers={"source_assertion_ids": ["as-cap-01"]},
        )
        self.assertEqual(dup_valid["relation"], "duplicates")

    def _seed_enterprise_test_scenarios(self) -> None:
        """Seed realistic enterprise scenarios across internal PartnerAdvisory and client Apex Global."""
        # Core Nodes
        self.conn.executescript("""
        -- Organizations
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, created_at, updated_at) VALUES
        ('org_nea', 'organization', 'Apex Global Analytics', 'NEA', 'Advanced analytics division', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('org_edo', 'organization', 'Enterprise Data Organization', 'EDO', 'Central data organization', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('org_pd', 'organization', 'Power Delivery', 'APEX Power Delivery', 'Grid operations and dispatch', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('org_fleet', 'organization', 'Fleet Operations', 'PGD Fleet Ops', 'Power generation fleet', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('org_care', 'organization', 'Customer Care', 'Customer Care', 'Customer call center & ops', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('org_partner_advisory', 'organization', 'PartnerAdvisory Consulting', 'PartnerAdvisory', 'Strategic technology consultancy', 'ACTIVE', '2026-09-12', '2026-09-12');

        -- Key Persons
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, created_at, updated_at) VALUES
        ('person_dan', 'person', 'Dan Smith', 'Dan S.', 'VP of Enterprise Architecture', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('person_greg', 'person', 'Greg Hannah', 'Greg H.', 'Director of Cloud Infrastructure', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('person_marcus', 'person', 'Marcus Vance', 'Marcus V.', 'Principal Grid Systems Architect', 'ACTIVE', '2026-09-12', '2026-09-12');

        -- Initiatives
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, created_at, updated_at) VALUES
        ('init_pubsub', 'initiative', 'Substation Real-Time Telemetry Streaming', 'Substation Streaming', 'GCP Pub/Sub sub-second event streaming', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_edo_batch', 'initiative', 'Enterprise Lakehouse Medallion Hub', 'EDO Lakehouse', 'Databricks Delta batch ingestion hub', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_pd_doc', 'initiative', 'Power Delivery Transformer Work Order AI', 'PD Work Order AI', 'Document extraction for work orders', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_fleet_doc', 'initiative', 'Turbine Maintenance Log Intelligence', 'Turbine Log AI', 'Maintenance document extraction', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_care_doc', 'initiative', 'Customer Billing Dispute Ingestion', 'Billing Ingestion AI', 'Customer bill statement parser', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_ai_platform', 'initiative', 'Enterprise GenAI Substrate', 'GenAI Substrate', 'Centralized LLM gateway and agent runtime', 'BLOCKED', '2026-09-12', '2026-09-12'),
        ('init_grid_dispatch', 'initiative', 'Autonomous Outage Dispatch Core', 'Outage Core', 'Critical outage restoration system', 'ACTIVE', '2026-09-12', '2026-09-12'),
        ('init_etrm_pricing', 'initiative', 'Apex Global Enterprises Trading & Risk Engine', 'ETRM Core', 'High-frequency energy pricing', 'ACTIVE', '2026-09-12', '2026-09-12');

        -- Org Ownership Edges
        INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, evidence_posture, confidence, materiality, created_at, updated_at) VALUES
        ('e_own_1', 'init_pubsub', 'OWNS', 'org_nea', 'NEA owns streaming telemetry', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_own_2', 'init_edo_batch', 'OWNS', 'org_edo', 'EDO owns enterprise lakehouse', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_own_3', 'init_pd_doc', 'OWNS', 'org_pd', 'PD owns work order ingestion', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_own_4', 'init_fleet_doc', 'OWNS', 'org_fleet', 'Fleet owns turbine logs', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_own_5', 'init_care_doc', 'OWNS', 'org_care', 'Care owns billing extraction', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12');
        """)

        # Seed Scenario 1: Cross-Silo Architectural Contradiction
        self.conn.execute("""
        INSERT INTO inter_initiative_edge (edge_id, tenant_id, from_initiative_id, relationship_type, to_initiative_id, evidence_statement, estimated_financial_waste_usd, created_at)
        VALUES ('iie_01', 'client:nee', 'init_pubsub', 'CONFLICTS_WITH', 'init_edo_batch',
                'NEA requires sub-second GCP Pub/Sub streaming for grid event protection, whereas EDO mandate enforces scheduled hourly batch Delta Live Tables.',
                4500000.0, '2026-09-12')
        """)

        # Seed Scenario 2: Duplicative Capability Software Spend
        self.conn.executescript("""
        INSERT INTO business_capability (capability_id, tenant_id, capability_level, canonical_name, business_domain, criticality_tier, created_at) VALUES
        ('cap_doc_ingest', 'client:nee', 2, 'Unstructured Document Intelligence Fabric', 'Enterprise AI Services', 'CORE_OPERATIONAL', '2026-09-12');

        INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, evidence_posture, confidence, materiality, created_at, updated_at) VALUES
        ('e_cap_1', 'init_pd_doc', 'IMPLEMENTS_CAPABILITY', 'cap_doc_ingest', 'PD work orders', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_cap_2', 'init_fleet_doc', 'IMPLEMENTS_CAPABILITY', 'cap_doc_ingest', 'Fleet logs', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_cap_3', 'init_care_doc', 'IMPLEMENTS_CAPABILITY', 'cap_doc_ingest', 'Customer billing', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12');

        INSERT INTO vendor_contract_dependency (contract_id, tenant_id, vendor_name, product_name, annual_spend_usd, renewal_date, lock_in_rating, governing_client_sponsor_id, created_at) VALUES
        ('v_pd', 'client:nee', 'DocumentAI Corp', 'DocExtractor Pro', 2200000.0, '2027-01-15', 'HIGH', 'init_pd_doc', '2026-09-12'),
        ('v_fleet', 'client:nee', 'CognitiveLog Systems', 'LogParse Enterprise', 2500000.0, '2026-11-30', 'EXTREME', 'init_fleet_doc', '2026-09-12'),
        ('v_care', 'client:nee', 'OmniParser Cloud', 'CareOCR Platform', 1500000.0, '2027-03-01', 'MODERATE', 'init_care_doc', '2026-09-12');

        INSERT INTO inter_initiative_edge (edge_id, tenant_id, from_initiative_id, relationship_type, to_initiative_id, evidence_statement, estimated_financial_waste_usd, created_at) VALUES
        ('iie_dup_1', 'client:nee', 'init_pd_doc', 'DUPLICATES', 'init_fleet_doc', 'Independent procurement of redundant OCR/LLM document pipelines.', 2500000.0, '2026-09-12');
        """)

        # Seed Scenario 3: Hidden Blocker & Detractor Stakeholder
        self.conn.executescript("""
        INSERT INTO architectural_decision (adr_id, tenant_id, decision_title, status, context_statement, selected_option, evaluated_options_json, decision_drivers_json, consequences_positive, consequences_negative, cost_of_reversal_score, formal_approver_id, informal_veto_id, created_at) VALUES
        ('adr_gpu_allocation', 'client:nee', 'Enterprise LLM Dedicated GPU Quotas', 'PROPOSED',
         'Mandate dedicated GPU cluster vs multi-tenant dynamic cloud endpoints.',
         'Multi-Tenant Dynamic Endpoints', '[]', '["FinOps"]', 'Low idle cost', 'Slight latency jitter', 3, 'person_dan', 'person_greg', '2026-09-12');

        INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, evidence_posture, confidence, materiality, created_at, updated_at) VALUES
        ('e_dep_ai', 'init_ai_platform', 'DEPENDS_ON', 'adr_gpu_allocation', 'AI platform blocked waiting for GPU cluster allocation decision', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12');

        INSERT INTO stakeholder_political_stance (stance_id, tenant_id, person_node_id, initiative_node_id, formal_role, effective_influence_tier, stance, primary_incentive, friction_driver, last_assessed_date, created_at) VALUES
        ('stance_greg', 'client:nee', 'person_greg', 'adr_gpu_allocation', 'Director of Cloud Infrastructure', 'INFORMAL_VETO', 'SKEPTIC', 'Cloud Budget Control', 'Uncapped inference cost runaway risk', '2026-09-12', '2026-09-12');
        """)

        # Seed Scenario 4: Key-Person Fragility
        self.conn.executescript("""
        INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, evidence_posture, confidence, materiality, created_at, updated_at) VALUES
        ('e_lead_1', 'person_marcus', 'SINGLE_EXPERT', 'init_grid_dispatch', 'Only person with dispatch firmware access', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_lead_2', 'person_marcus', 'LEADS', 'init_etrm_pricing', 'Leads pricing engine algorithm design', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12'),
        ('e_lead_3', 'person_marcus', 'OWNS', 'init_pubsub', 'Architect of substation telemetry broker', 'CONFIRMED', 'HIGH', 'HIGH', '2026-09-12', '2026-09-12');

        INSERT INTO technical_debt_item (debt_id, tenant_id, title, debt_type, affected_node_id, annual_carrying_cost_usd, remediation_cost_usd, blast_radius_score, remediation_priority, created_at) VALUES
        ('td_dispatch_legacy', 'client:nee', 'Undocumented SCADA Polling Protocol', 'PROTOCOL_OBSOLESCENCE', 'init_grid_dispatch', 850000.0, 300000.0, 5, 'P0_IMMEDIATE', '2026-09-12');
        """)

        # Seed Scenario 5: Reusable IP Assets (PartnerAdvisory IP)
        self.conn.executescript("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, knowledge_role, applicability_scope, created_at) VALUES
        ('as_re_1', 'init_pubsub', 'supports', 'capability', 'generalizable', '2026-09-12'),
        ('as_re_2', 'init_pubsub', 'implements', 'external_pattern', 'generalizable', '2026-09-12'),
        ('as_re_3', 'init_pubsub', 'achieves', 'target_outcome', 'generalizable', '2026-09-12');
        """)
        self.conn.commit()

    def test_03_golden_cto_queries_and_executive_synthesis(self) -> None:
        """Verify that all 5 Golden CTO Queries extract exact insights from seeded scenarios."""
        self._seed_enterprise_test_scenarios()
        engine = ExecutiveAnalyticsEngine(self.conn)

        # 1. Contradiction Query
        contradictions = engine.detect_cross_silo_contradictions(tenant_id="client:nee")
        self.assertEqual(len(contradictions), 1)
        c = contradictions[0]
        self.assertEqual(c["initiative_alpha"], "Substation Real-Time Telemetry Streaming")
        self.assertEqual(c["initiative_beta"], "Enterprise Lakehouse Medallion Hub")
        self.assertEqual(c["estimated_financial_waste_usd"], 4500000.0)
        self.assertIn("bridging contract", c["strategic_recommendation"])

        # 2. Duplication Query
        duplications = engine.harvest_duplicative_platform_spend(tenant_id="client:nee")
        self.assertEqual(len(duplications), 1)
        d = duplications[0]
        self.assertEqual(d["business_capability"], "Unstructured Document Intelligence Fabric")
        self.assertEqual(d["separate_implementations"], 3)
        self.assertEqual(d["combined_vendor_spend_usd"], 6200000.0)
        self.assertIn("Consolidate 3 disparate implementations", d["strategic_recommendation"])

        # 3. Decision Blocker Query
        blockers = engine.trace_hidden_decision_blockers(tenant_id="client:nee")
        self.assertEqual(len(blockers), 1)
        b = blockers[0]
        self.assertEqual(b["initiative_name"], "Enterprise GenAI Substrate")
        self.assertEqual(b["decision_title"], "Enterprise LLM Dedicated GPU Quotas")
        self.assertEqual(b["blocking_person_name"], "Greg Hannah")
        self.assertEqual(b["stakeholder_stance"], "SKEPTIC")
        self.assertIn("FinOps/risk control framework", b["strategic_recommendation"])

        # 4. Key-Person Fragility Query
        fragility = engine.analyze_key_person_fragility()
        self.assertEqual(len(fragility), 1)
        f = fragility[0]
        self.assertEqual(f["key_person"], "Marcus Vance")
        self.assertEqual(f["critical_systems_owned"], 3)
        self.assertIn("knowledge externalization", f["strategic_recommendation"])

        # 5. Reusable IP Asset Query
        reusable = engine.identify_reusable_ip_assets()
        self.assertEqual(len(reusable), 1)
        r = reusable[0]
        self.assertEqual(r["component_name"], "Substation Real-Time Telemetry Streaming")
        self.assertEqual(r["verified_assertions"], 3)
        self.assertEqual(r["is_generalizable"], 1)

        # Full Executive Analysis Packet
        full_analysis = engine.run_all_executive_analyses(tenant_id="client:nee")
        metrics = full_analysis["summary_metrics"]
        self.assertGreater(metrics["total_identified_financial_waste_usd"], 6000000.0)
        self.assertEqual(metrics["architectural_contradictions_count"], 1)
        self.assertEqual(metrics["duplicative_capabilities_count"], 1)
        self.assertEqual(metrics["hidden_decision_blockers_count"], 1)
        self.assertEqual(metrics["key_person_vulnerabilities_count"], 1)
        self.assertEqual(metrics["reusable_ip_accelerators_count"], 1)

        # Markdown Executive Brief
        brief = engine.format_executive_brief_markdown(full_analysis)
        self.assertIn("# Executive Strategic Intelligence Briefing", brief)
        self.assertIn("Substation Real-Time Telemetry Streaming", brief)
        self.assertIn("Unstructured Document Intelligence Fabric", brief)
        self.assertIn("Greg Hannah", brief)

    def test_04_cli_executive_insights(self) -> None:
        """Verify CLI execution via nee executive-insights."""
        self._seed_enterprise_test_scenarios()

        cmd = [
            sys.executable,
            str(ROOT / "nee.py"),
            "executive-insights",
            "--graph",
            str(self.db_path),
            "--pattern",
            "all",
            "--format",
            "json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            result.returncode,
            0,
            f"CLI failed with code {result.returncode}. Stderr: {result.stderr}",
        )

        data = json.loads(result.stdout)
        self.assertIn("summary_metrics", data)
        self.assertEqual(data["summary_metrics"]["architectural_contradictions_count"], 1)
        self.assertEqual(data["summary_metrics"]["duplicative_capabilities_count"], 1)


if __name__ == "__main__":
    unittest.main()
