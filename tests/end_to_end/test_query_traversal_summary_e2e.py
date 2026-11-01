"""End-to-end test suite for external-ready query prose and graph traversal summary.

Verifies that knowledge query responses adhere to INV-QUERY-CLEAN-PROSE:
1. Narrative prose contains zero inline footnote brackets ([1], [2]), raw assertion hashes, or node IDs.
2. Traversal evidence is consolidated into a compact bottom summary callout and deduplicated source table.
3. Full integration with validate_query_output_format and delivery guard boundary certification.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.query_core.lineage_formatter import (
    LINEAGE_SECTION_HEADER,
    format_seamless_query_response,
    validate_seamless_prose,
)
from tools.validate_query_output_format import validate_query_markdown


AGENTIC_MANAGED_SERVICES_QUERY_INPUT = """
### Strategic Overview: Agentic Managed Services

In PartnerAdvisory’s modern operating model ([`model_agentic_managed_services`](file:///tmp/ams_graph.sqlite)),
**Agentic Managed Services** transitions consulting engagements from passive labor augmentation to
outcome-oriented cognitive pipelines ([`ast_ing_ams_outcome_roi_01`](file:///tmp/ams_graph.sqlite)).
The primary value proposition centers on autonomous agent loops executing continuous data monitoring,
anomaly detection, and governed decision workflows ([`ast_ing_ams_auto_loops_02`](file:///tmp/ams_graph.sqlite)).

### Organizational Roles & Leadership

Within the Agentic Managed Services framework, the Solutions Architect partners with client leadership
to define strict verification boundaries ([`asrt_claim_solutions_architect_gov`](file:///tmp/ams_graph.sqlite)).
Technical oversight for the implementation is PartnerAdvisory-led by Lead Architect Sarah Jenkins ([`asrt_edge_sarah_jenkins_ams`](file:///tmp/ams_graph.sqlite)).
Project Sentinel ([`workstream_sentinel`](file:///tmp/ams_graph.sqlite)) serves as the core delivery vehicle,
integrating telemetry sidecars into client production workloads ([`sow_sentinel_phase1`](file:///tmp/ams_graph.sqlite)).

### Governance & Verification Gates

To ensure enterprise-grade safety, all agentic recommendations require dual-key verification before writeback
([`assertion-ams-dual-key-verification`](file:///tmp/ams_graph.sqlite)). Human-in-the-loop review queues
prevent uncorroborated autonomous actions from polluting the operational graph ([`assertion-ams-hitl-safeguard`](file:///tmp/ams_graph.sqlite)).
"""


@pytest.fixture
def ams_isolated_db(tmp_path: Path) -> Path:
    """Provisions an isolated SQLite knowledge graph for the Agentic Managed Services scenario."""
    db_file = tmp_path / "ams_graph.sqlite"
    conn = sqlite3.connect(str(db_file))
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT,
                canonical_uri TEXT,
                cold_archive_ref TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                currentness_state TEXT,
                primary_source_id TEXT,
                legacy_claim_id TEXT
            );

            INSERT INTO node VALUES ('model_agentic_managed_services', 'Agentic Managed Services', 'operating_model');
            INSERT INTO node VALUES ('workstream_sentinel', 'Project Sentinel', 'workstream');
            INSERT INTO node VALUES ('sow_sentinel_phase1', 'Sentinel Phase 1 Integration', 'sow');
            INSERT INTO node VALUES ('person_sarah_jenkins', 'Sarah Jenkins', 'person');

            INSERT INTO source VALUES ('src_ams_strategy_2026', 'PartnerAdvisory 2026 Agentic Strategy Whitepaper', 'file:///tmp/ams_strategy.md', NULL);
            INSERT INTO source VALUES ('src_sentinel_sow_doc', 'Project Sentinel SOW & Architecture Spec', 'file:///tmp/sentinel_spec.md', NULL);

            INSERT INTO assertion VALUES (
                'ast_ing_ams_outcome_roi_01', 'model_agentic_managed_services', 'DELIVERS_OUTCOME', NULL,
                'Outcome-oriented cognitive pipelines replacing passive labor', 'ROI and outcome model',
                'HIGH', 0.94, 'canonical', 1, 'approved', 'current', 'src_ams_strategy_2026', NULL
            );
            INSERT INTO assertion VALUES (
                'ast_ing_ams_auto_loops_02', 'model_agentic_managed_services', 'EXECUTES_WORKFLOW', NULL,
                'Autonomous agent loops executing continuous monitoring', 'Autonomous monitoring loops',
                'HIGH', 0.92, 'canonical', 1, 'approved', 'current', 'src_ams_strategy_2026', NULL
            );
            INSERT INTO assertion VALUES (
                'asrt_claim_solutions_architect_gov', 'model_agentic_managed_services', 'GOVERNS', NULL,
                'Solutions Architect defines strict verification boundaries', 'Architect governance role',
                'CONFIRMED', 1.0, 'canonical', 1, 'approved', 'current', 'src_ams_strategy_2026', NULL
            );
            INSERT INTO assertion VALUES (
                'asrt_edge_sarah_jenkins_ams', 'person_sarah_jenkins', 'LEADS_TECHNICAL_DELIVERY', 'workstream_sentinel',
                NULL, 'Sarah Jenkins leads Sentinel technical delivery',
                'HIGH', 0.95, 'canonical', 1, 'approved', 'current', 'src_sentinel_sow_doc', NULL
            );
            INSERT INTO assertion VALUES (
                'assertion-ams-dual-key-verification', 'workstream_sentinel', 'REQUIRES_GATE', NULL,
                'Dual-key verification before enterprise writeback', 'Dual-key safety gate',
                'HIGH', 0.96, 'canonical', 1, 'approved', 'current', 'src_sentinel_sow_doc', NULL
            );
            INSERT INTO assertion VALUES (
                'assertion-ams-hitl-safeguard', 'workstream_sentinel', 'ENFORCES_SAFEGUARD', NULL,
                'Human-in-the-loop review queues prevent graph pollution', 'HITL safeguard queue',
                'HIGH', 0.93, 'canonical', 1, 'approved', 'current', 'src_sentinel_sow_doc', NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    return db_file


def test_agentic_managed_services_external_ready_prose_e2e(ams_isolated_db: Path) -> None:
    """Verifies that an Agentic Managed Services query formats into external-ready prose with consolidated summary."""
    result = format_seamless_query_response(
        AGENTIC_MANAGED_SERVICES_QUERY_INPUT,
        graph_path=str(ams_isolated_db),
    )

    # 1. Clean narrative prose verification (zero footnotes, zero raw IDs)
    assert "[1]" not in result.clean_prose
    assert "[2]" not in result.clean_prose
    assert "[" not in result.clean_prose
    assert "ast_ing_ams" not in result.clean_prose
    assert "model_agentic_managed_services" not in result.clean_prose
    assert "file:///" not in result.clean_prose
    assert "Project Sentinel" in result.clean_prose
    assert "Sarah Jenkins" in result.clean_prose

    is_valid_prose, violations_prose = validate_seamless_prose(result.clean_prose)
    assert is_valid_prose, f"Prose validation failed: {violations_prose}"

    # 2. Consolidated graph traversal summary footer
    assert LINEAGE_SECTION_HEADER in result.lineage_footer
    assert "**Graph Traversal Summary:**" in result.lineage_footer
    assert "| Source / Domain | Entities & Workstreams Traversed | Assertions | Confidence |" in result.lineage_footer
    assert "PartnerAdvisory 2026 Agentic Strategy Whitepaper" in result.lineage_footer
    assert "Project Sentinel SOW & Architecture Spec" in result.lineage_footer

    # 3. Overall confidence and traversal counts
    assert "6 assertions evaluated" in result.lineage_footer
    assert "2 distinct sources" in result.lineage_footer

    # 4. Strict validator gate passes
    is_valid_md, violations_md = validate_query_markdown(result.formatted_markdown)
    assert is_valid_md, f"Query markdown validator failed: {violations_md}"


def test_agentic_managed_services_decision_envelope_integration(ams_isolated_db: Path) -> None:
    """Verifies end-to-end integration with dual_pipeline delivery envelope unwrapping."""
    from dual_pipeline.cli.engine import _apply_seamless_lineage_formatting

    envelope = {
        "artifact_id": "ams-decision-envelope-e2e",
        "contract_id": "urn:nee:dual-pipeline:decision-response-envelope:1.0.0",
        "destination": "GRAPH_EVIDENCE_UTILITY",
        "result": {
            "answer": {
                "current_state": [
                    {
                        "statement": "Agentic Managed Services enforces autonomous monitoring loops",
                        "citations": ["ast_ing_ams_auto_loops_02", "model_agentic_managed_services"],
                    }
                ],
                "opportunities": [],
                "pain_points": [],
                "guidance": [],
                "hypotheses": [],
                "missing_information": [],
            },
            "x3_outcome": "X3D_ALLOW_FINISH",
        },
    }

    _apply_seamless_lineage_formatting(envelope, graph_path=str(ams_isolated_db))

    assert "formatted_markdown" in envelope
    assert "clean_prose" in envelope
    assert "lineage_footer" in envelope
    assert "[1]" not in envelope["clean_prose"]
    assert "**Graph Traversal Summary:**" in envelope["lineage_footer"]
    assert "PartnerAdvisory 2026 Agentic Strategy Whitepaper" in envelope["lineage_footer"]
