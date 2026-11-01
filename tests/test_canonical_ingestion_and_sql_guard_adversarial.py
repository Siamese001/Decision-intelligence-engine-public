"""Wave 1 Adversarial & Regression Suite:
- Cross-object false supersession restoration (66 NEA vs 40 EDO/NEE IT engineers)
- Candidate retrieval_eligible=0 enforcement on write and remediation
- Legacy claim table supersession sync and read guard
- Pre-tool hook & MCP graphdb_read_query canonical/currentness SQL enforcement
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.graph_writer_core.assertions import AssertionOpsMixin
from scripts.hitl_pre_tool_guard import _is_unfiltered_assertion_sql, handle_tool_call_guard
from tools.mcp_graphdb_server import _filter_sanitized_rows, graphdb_read_query
from tools.remediate_footprint_cohydrated_supersession import run_remediation


def _seed_sandbox_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            validation_state TEXT,
            currentness_state TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            confidence_label TEXT,
            confidence_score REAL,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE claim (
            claim_id TEXT PRIMARY KEY,
            claim_text TEXT,
            confidence TEXT,
            superseded_by TEXT
        )"""
    )
    # 1. 66 NEA engineers (canonical, current)
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_edge_896ad3f3aa363e3fd11aaa46', 'staffing_footprint_partner_advisory_nee',
            'CONTRIBUTES_TO', 'org_nea', NULL, 'canonical', 1, 1,
            'accepted_legacy', 'current', NULL, NULL, 'confirmed', 1.0, '2026-10-06T00:00:00Z'
        )"""
    )
    # 2. 40 EDO/NEE IT engineers erroneously superseded by the 66 NEA edge
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_edge_ce678f8934e40e77104d89ca', 'staffing_footprint_partner_advisory_nee',
            'CONTRIBUTES_TO', 'org_edo_and_nee_it', NULL, 'canonical', 1, 0,
            'superseded', 'historical', 'asrt_edge_896ad3f3aa363e3fd11aaa46',
            '2026-10-06T00:00:00Z', 'confirmed', 1.0, '2026-10-06T00:00:00Z'
        )"""
    )
    # 3. Superseded candidate claim v31 cohydrated model with stale legacy claim row
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt_claim_claim_v31_cohydrated_model_selected', 'model_cohydrated_operating_model',
            'HAS_CLAIM_TEXT', NULL, 'Co-hydrated NEA-PartnerAdvisory Product + Engineering model',
            'candidate', 0, 1, 'superseded', 'historical',
            'asrt_claim_claim_v35_cohydrated_direction_solution_pending',
            '2026-10-06T08:49:49Z', 'confirmed', 1.0, '2026-10-06T08:49:49Z'
        )"""
    )
    conn.execute(
        """INSERT INTO search_document VALUES (
            'sd_v31', 'asrt_claim_claim_v31_cohydrated_model_selected',
            'candidate', 'current', 1, '2026-10-06T08:49:49Z'
        )"""
    )
    conn.execute(
        """INSERT INTO claim VALUES (
            'claim_v31_cohydrated_model_selected',
            'Co-hydrated NEA-PartnerAdvisory Product + Engineering model',
            'confirmed', NULL
        )"""
    )
    conn.execute(
        """INSERT INTO claim VALUES (
            'claim_v35_cohydrated_direction_solution_pending',
            'Co-hydration remains the confirmed strategic direction, detailed alignment pending Q4',
            'confirmed', NULL
        )"""
    )
    conn.commit()
    conn.close()


def test_remediation_restores_40_engineer_edge_and_demotes_candidates() -> None:
    """Exact production regression: restore cross-object false supersession and sync legacy claims."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
        db_path = Path(tmp) / "graph.sqlite"
        receipt_path = Path(tmp) / "receipt.json"
        _seed_sandbox_db(db_path)

        res = run_remediation(db_path, receipt_path)
        assert res["remediation_counts"]["restored_false_supersessions"] == 1
        assert res["remediation_counts"]["demoted_non_canonical_retrieval_eligible"] == 1
        assert res["remediation_counts"]["synced_legacy_claims"] == 1
        assert receipt_path.is_file()

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row_40 = conn.execute(
            "SELECT validation_state, currentness_state, superseded_by_assertion_id, retrieval_eligible "
            "FROM assertion WHERE assertion_id = 'asrt_edge_ce678f8934e40e77104d89ca'"
        ).fetchone()
        assert row_40 == ("accepted_legacy", "current", None, 1)

        bad_elig = conn.execute(
            "SELECT COUNT(*) FROM assertion WHERE (canonical_status != 'canonical' OR currentness_state != 'current') AND retrieval_eligible = 1"
        ).fetchone()[0]
        assert bad_elig == 0

        claim_row = conn.execute(
            "SELECT superseded_by FROM claim WHERE claim_id = 'claim_v31_cohydrated_model_selected'"
        ).fetchone()
        assert claim_row == ("claim_v35_cohydrated_direction_solution_pending",)
        conn.close()


def test_assertion_writer_builds_candidate_with_zero_retrieval_eligible() -> None:
    """Verify candidate assertions built via _assertion_values never get retrieval_eligible=1."""
    payload = {
        "disposition": "CANDIDATE",
        "subject_node_id": "workstream_lens",
        "predicate": "HAS_RISK",
        "object_node_id": "risk_1",
        "retrieval_eligible": True,
        "candidate": {
            "candidate_id": "cand_1",
            "candidate_type": "edge_assertion",
            "normalized_text": "Unpromoted single-source hypothesis",
            "materiality": "high",
            "classification": "internal",
            "extraction_confidence": 0.95,
            "audiences": ["internal"],
        },
    }
    source = {
        "source_id": "src_1",
        "source_version_id": "sv_1",
        "date": "2026-10-21",
    }
    vals = AssertionOpsMixin._assertion_values(
        payload=payload,
        target_id="asrt_cand_1",
        source=source,
        extraction_run_id="run_1",
        now="2026-10-21T00:00:00Z",
        validation_state="unreviewed",
    )
    # Index 28: canonical_status='candidate', Index 29: canonical_eligible=0, Index 30: retrieval_eligible=0
    assert vals[28] == "candidate"
    assert vals[29] == 0
    assert vals[30] == 0


@pytest.mark.parametrize(
    "sql_query,expected_blocked",
    [
        ("", False),
        ("PRAGMA table_info(assertion)", False),
        ("SELECT COUNT(*) FROM assertion", False),
        ("SELECT * FROM claim WHERE claim_id LIKE '%cohydrated%'", True),
        ("SELECT * FROM assertion WHERE subject_node_id = 'org_nea'", True),
        (
            "SELECT * FROM assertion WHERE canonical_status = 'canonical' AND canonical_eligible = 1",
            True,  # missing currentness_state
        ),
        (
            "SELECT * FROM assertion WHERE canonical_status = 'canonical' AND canonical_eligible = 1 AND currentness_state = 'current'",
            False,
        ),
    ],
)
def test_pre_tool_guard_sql_filter_boundary_and_bypass(sql_query: str, expected_blocked: bool) -> None:
    """Boundary, malformed, and invariant-bypass tests for _is_unfiltered_assertion_sql and handle_tool_call_guard."""
    assert _is_unfiltered_assertion_sql(sql_query) is expected_blocked

    # Malformed JSON input
    assert handle_tool_call_guard("{truncated_json") is None
    assert handle_tool_call_guard("") is None

    # MCP call_mcp_tool payload check
    mcp_payload = json.dumps(
        {
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ToolName": "graphdb_read_query",
                    "Arguments": {"query": sql_query, "include_candidates": False},
                },
            }
        }
    )
    res = handle_tool_call_guard(mcp_payload)
    assert res is not None
    if expected_blocked:
        assert res["decision"] == "deny"
        assert "CANONICAL_QUERY_FILTER_REQUIRED" in res["reason"]
    else:
        assert res["decision"] == "allow"


def test_mcp_graphdb_read_query_blocks_legacy_claim_and_filters_candidates() -> None:
    """Verify graphdb_read_query blocks legacy claim table and filters out candidate/historical assertions."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
        db_path = Path(tmp) / "graph.sqlite"
        _seed_sandbox_db(db_path)

        # 1. Direct query against legacy claim table is blocked when include_candidates=False
        claim_out = graphdb_read_query(
            "SELECT * FROM claim WHERE claim_id = 'claim_v31_cohydrated_model_selected'",
            db_path=str(db_path),
            include_candidates=False,
        )
        assert "LEGACY_CLAIM_TABLE_BLOCKED" in claim_out

        # 2. Query against assertion filters out candidate and historical rows automatically
        asrt_out = json.loads(
            graphdb_read_query(
                "SELECT assertion_id, subject_node_id FROM assertion",
                db_path=str(db_path),
                include_candidates=False,
            )
        )
        returned_ids = [r["assertion_id"] for r in asrt_out["rows"]]
        assert returned_ids == ["asrt_edge_896ad3f3aa363e3fd11aaa46"]

        # 3. _filter_sanitized_rows excludes rows with non-empty superseded_by
        filtered = _filter_sanitized_rows(
            [{"claim_id": "c1", "superseded_by": "c2"}, {"claim_id": "c2", "superseded_by": None}],
            include_candidates=False,
        )
        assert [r["claim_id"] for r in filtered] == ["c2"]
