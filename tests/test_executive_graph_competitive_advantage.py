#!/usr/bin/env python3
"""tests/test_executive_graph_competitive_advantage.py

Unit and integration test suite validating GraphDB executive competitive advantage:
- Canonical schema extensions (executive_decision_impact, strategic_value_stream, thought_leadership_blueprint)
- Shadow schema extensions (stakeholder_influence_map, career_leverage_enclave)
- Multi-hop architectural blast-radius assessment
- Cross-silo contradiction mining
- C-suite decision memorandum generation and Rule C-19 quarantine enforcement
- Thought leadership blueprint extraction
"""
from __future__ import annotations

import sqlite3

import pytest

from dual_pipeline.executive_decision_exoskeleton import (
    assess_change_impact,
    detect_cross_silo_contradictions,
    extract_thought_leadership_blueprints,
    generate_csuite_decision_memorandum,
)
from storage.models import (
    ExecutiveDecisionImpact,
    StrategicValueStream,
)
from storage.schema_canonical import init_canonical_schema
from storage.schema_shadow import init_shadow_schema


@pytest.fixture
def memory_db():
    """Provides an isolated in-memory database with canonical and shadow schemas initialized."""
    conn = sqlite3.connect(":memory:")
    init_canonical_schema(conn)
    init_shadow_schema(conn)
    yield conn
    conn.close()


class TestExecutiveGraphCompetitiveAdvantage:
    """Validates the executive cognitive exoskeleton capabilities."""

    def test_canonical_and_shadow_schema_initialization(self, memory_db):
        """Verifies that all 5 new executive tables and indexes are initialized."""
        tables = [r[0] for r in memory_db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "executive_decision_impact" in tables
        assert "strategic_value_stream" in tables
        assert "thought_leadership_blueprint" in tables
        assert "stakeholder_influence_map" in tables
        assert "career_leverage_enclave" in tables

    def test_executive_models_serialization(self):
        """Verifies dataclass instantiation and dictionary serialization."""
        impact = ExecutiveDecisionImpact(
            impact_id="imp_test",
            decision_node_id="dec_test",
            capability_l1="Grid Operations",
            financial_value_usd=500000.0,
            risk_blast_radius_tier="HIGH",
        )
        d = impact.to_dict()
        assert d["financial_value_usd"] == 500000.0
        assert d["risk_blast_radius_tier"] == "HIGH"

        stream = StrategicValueStream(
            stream_id="vs_test",
            stream_name="Outage Prevention",
            business_domain="Power Delivery",
            lead_executive_sponsor="VP Grid",
            revenue_impact_tier="CORE_REVENUE",
        )
        assert stream.to_dict()["revenue_impact_tier"] == "CORE_REVENUE"

    def test_assess_change_impact_multi_hop(self, memory_db):
        """Verifies cycle-free multi-hop traversal and blast radius calculation."""
        # 1. Populate node topology: Decision -> Asset A -> Asset B -> Asset C
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('dec:1', 'ev1', 'decision', 'Cloud Migration')")
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('sys:core_api', 'ev1', 'system', 'Core Broker API')")
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('sys:substation', 'ev1', 'system', 'Substation Telemetry')")
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('sys:billing', 'ev1', 'system', 'Customer Billing')")

        # 2. Add edges
        memory_db.execute("INSERT INTO canonical_graph_edges (edge_id, from_node_id, predicate, to_node_id) VALUES ('e1', 'dec:1', 'CHANGES', 'sys:core_api')")
        memory_db.execute("INSERT INTO canonical_graph_edges (edge_id, from_node_id, predicate, to_node_id) VALUES ('e2', 'sys:core_api', 'DEPENDS_ON', 'sys:substation')")
        memory_db.execute("INSERT INTO canonical_graph_edges (edge_id, from_node_id, predicate, to_node_id) VALUES ('e3', 'sys:substation', 'FEEDS', 'sys:billing')")
        # Add cycle to verify cycle-freedom
        memory_db.execute("INSERT INTO canonical_graph_edges (edge_id, from_node_id, predicate, to_node_id) VALUES ('e4', 'sys:billing', 'REFERENCES', 'sys:core_api')")

        # 3. Add financial impact
        memory_db.execute("""
        INSERT INTO executive_decision_impact (impact_id, decision_node_id, capability_l1, financial_value_usd, risk_blast_radius_tier)
        VALUES ('imp:1', 'dec:1', 'Cloud Infrastructure', 2500000.0, 'CRITICAL')
        """)

        # 4. Add strategic value stream
        memory_db.execute("""
        INSERT INTO strategic_value_stream (stream_id, stream_name, business_domain, lead_executive_sponsor, underlying_technical_assets)
        VALUES ('vs:1', 'Customer Revenue Realization', 'Billing & Finance', 'CFO', 'Customer Billing,Core Broker API')
        """)

        result = assess_change_impact(memory_db, "dec:1", asset_name="Cloud Migration", max_hops=4)
        assert result["reachable_asset_count"] == 4
        assert result["overall_blast_radius_tier"] == "CRITICAL"
        assert result["financial_exposure_usd"] == 2500000.0
        assert len(result["linked_value_streams"]) == 1
        assert result["linked_value_streams"][0]["stream_name"] == "Customer Revenue Realization"

    def test_detect_cross_silo_contradictions(self, memory_db):
        """Verifies detection of conflicting assertions on the same subject."""
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('sys:lakehouse', 'ev1', 'system', 'Enterprise Lakehouse')")
        memory_db.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, literal_value, assertion_type, status, observed_at)
        VALUES ('as:1', 'sys:lakehouse', 'ENCRYPTION_STANDARD', 'AES-256-GCM', 'TECHNICAL', 'CONFIRMED', '2026-09-01')
        """)
        memory_db.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, literal_value, assertion_type, status, observed_at)
        VALUES ('as:2', 'sys:lakehouse', 'ENCRYPTION_STANDARD', 'PLAINTEXT_PROTOTYPE', 'TECHNICAL', 'CONFIRMED', '2026-09-15')
        """)

        contradictions = detect_cross_silo_contradictions(memory_db)
        assert len(contradictions) == 1
        assert contradictions[0]["subject_node_id"] == "sys:lakehouse"
        assert contradictions[0]["dialectical_state"] == "ACTIVE_TENSION"

    def test_generate_csuite_decision_memorandum_rule_c19(self, memory_db):
        """Verifies C-suite memorandum generation and Rule C-19 audience isolation."""
        memory_db.execute("INSERT INTO canonical_graph_nodes (node_id, evidence_id, type, name) VALUES ('dec:modernize', 'ev1', 'decision', 'Core ERP Modernization')")
        memory_db.execute("""
        INSERT INTO assertion (assertion_id, subject_node_id, predicate, literal_value, assertion_type, status, observed_at)
        VALUES ('as:mod', 'dec:modernize', 'BUDGET_CAP', '$12M Approved', 'DECISION', 'CONFIRMED', '2026-09-10')
        """)
        memory_db.execute("""
        INSERT INTO stakeholder_influence_map (influence_id, stakeholder_name_hmac, informal_role, title_authority_gap, sentiment_trajectory)
        VALUES ('inf:1', 'hmac_vp_ops', 'INFORMAL_VETO', 'HIGH_INFLUENCE_LOW_TITLE', 'RESISTANT')
        """)

        # 1. Client-safe audience query: shadow context must be quarantined
        memo_client = generate_csuite_decision_memorandum(memory_db, "dec:modernize", "Core ERP Modernization", audience="CLIENT_SAFE")
        assert memo_client["audience"] == "CLIENT_SAFE"
        assert memo_client["internal_advisory_context"] == "STRICTLY_QUARANTINED"
        assert memo_client["rule_c19_firewall_status"] == "VERIFIED_ISOLATED"
        assert len(memo_client["evidence_provenance"]["assertions"]) == 1

        # 2. Internal advisory audience query: shadow context is accessible
        memo_internal = generate_csuite_decision_memorandum(memory_db, "dec:modernize", "Core ERP Modernization", audience="INTERNAL_ADVISORY")
        assert memo_internal["audience"] == "INTERNAL_ADVISORY"
        assert isinstance(memo_internal["internal_advisory_context"], list)
        assert len(memo_internal["internal_advisory_context"]) == 1
        assert memo_internal["internal_advisory_context"][0]["role"] == "INFORMAL_VETO"

    def test_extract_thought_leadership_blueprints(self, memory_db):
        """Verifies extraction and status filtering of publication blueprints."""
        memory_db.execute("""
        INSERT INTO thought_leadership_blueprint (blueprint_id, title, target_publication, abstract_concept, anonymized_industry_patterns, architectural_exhibits, executive_takeaway, status)
        VALUES ('tl:hbr', 'Accountable Agentic Architecture', 'MIT Sloan Management Review', 'Authority in AI Governance', '[\"Bounded Agency\"]', '[\"Governance DAG\"]', 'Autonomous systems require explicit authority boundaries', 'READY_FOR_PUBLICATION')
        """)
        memory_db.execute("""
        INSERT INTO thought_leadership_blueprint (blueprint_id, title, target_publication, abstract_concept, anonymized_industry_patterns, architectural_exhibits, executive_takeaway, status)
        VALUES ('tl:draft', 'Unfinished Thoughts', 'Drafts', 'Concept', '[]', '[]', 'TBD', 'DRAFT')
        """)

        published = extract_thought_leadership_blueprints(memory_db, status_filter="READY_FOR_PUBLICATION")
        assert len(published) == 1
        assert published[0]["title"] == "Accountable Agentic Architecture"
        assert published[0]["target_publication"] == "MIT Sloan Management Review"
