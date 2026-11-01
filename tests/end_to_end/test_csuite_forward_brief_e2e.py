"""End-to-end verification of C-Suite Forward Brief Generator and Private Advisory.

Verifies:
1. Complete top-down forward briefings generated across all Apex Global enterprise leaders:
   Mark Lemasney (PGD), Ben Miron (CIO), Rich Argentieri (CPO), Matt Barrows,
   Chakra Sankaraiah, logan Miller, and Ashish Gupta (Dominion).
2. Audience isolation: INTERNAL_ADVISORY surfaces shadow influence / power maps,
   while CLIENT_SAFE quarantines private internal notes.
3. The 'nee executive-brief' CLI executes cleanly across text and JSON formats.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.cli.executive_brief_handlers import handle_executive_brief_cli
from dual_pipeline.executive_decision_exoskeleton import generate_top_down_csuite_brief


@pytest.fixture
def sandbox_env():
    with tempfile.NamedTemporaryFile(suffix="_brief_cli_test.sqlite", delete=False) as tf:
        db_path = tf.name
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stakeholder_influence_map (
            influence_id TEXT PRIMARY KEY,
            informal_role TEXT,
            title_authority_gap TEXT,
            sentiment_trajectory TEXT,
            single_point_of_failure_risk INTEGER,
            is_quarantined INTEGER,
            namespace TEXT
        );
    """)
    conn.execute("""
        INSERT INTO stakeholder_influence_map VALUES
        ('inf_1', 'Executive Sponsor', 'High informal veto power on cloud spending', 'POSITIVE', 0, 0, 'INTERNAL_ADVISORY');
    """)
    conn.commit()
    yield conn, db_path
    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_forward_briefs_across_all_enterprise_leaders_e2e(sandbox_env):
    conn, _ = sandbox_env
    leaders = [
        ("Mark Lemasney", "POWER_GENERATION_DIVISION"),
        ("Ben Miron", "ONE_TECH_CORE"),
        ("Richard Argentieri", "ONE_TECH_PRODUCTS"),
        ("Matt Barrows", "ONE_TECH_PRODUCTS"),
        ("Chakra Sankaraiah", "ONE_TECH_CORE"),
        ("logan Miller", "ONE_TECH_CORE"),
        ("Ashish Gupta", "DOMINION_INTEGRATION"),
    ]

    for name, expected_bu in leaders:
        brief = generate_top_down_csuite_brief(conn, name, audience="INTERNAL_ADVISORY")
        assert brief["primary_bu"] == expected_bu
        assert brief["meets_strategic_floor"] is True
        assert brief["strategic_foresight_ratio"] >= 0.30
        assert len(brief["proactive_whitespace_plays"]) >= 3
        # Invariant: First play has highest SOW value
        sow_vals = [p.get("sow_value", 0.0) for p in brief["proactive_whitespace_plays"]]
        assert sow_vals[0] == max(sow_vals)


def test_audience_isolation_internal_advisory_vs_client_safe(sandbox_env):
    conn, _ = sandbox_env

    # 1. INTERNAL_ADVISORY audience surfaces power map
    internal_brief = generate_top_down_csuite_brief(conn, "Mark Lemasney", audience="INTERNAL_ADVISORY")
    assert internal_brief["audience"] == "INTERNAL_ADVISORY"
    assert isinstance(internal_brief["internal_advisory_power_map"], list)
    assert len(internal_brief["internal_advisory_power_map"]) >= 1

    # 2. CLIENT_SAFE audience strictly quarantines private power maps
    client_brief = generate_top_down_csuite_brief(conn, "Mark Lemasney", audience="CLIENT_SAFE")
    assert client_brief["audience"] == "CLIENT_SAFE"
    assert client_brief["internal_advisory_power_map"] == "STRICTLY_QUARANTINED"


def test_nee_executive_brief_cli_execution_e2e(sandbox_env):
    _, db_path = sandbox_env

    # Test JSON output
    stdout_buf = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout_buf
        ret_code = handle_executive_brief_cli([
            "--stakeholder", "Ben Miron",
            "--format", "json",
            "--db", db_path,
        ])
    finally:
        sys.stdout = old_stdout

    assert ret_code == 0
    parsed = json.loads(stdout_buf.getvalue())
    assert parsed["executive_name"] == "Ben Miron"
    assert parsed["primary_bu"] == "ONE_TECH_CORE"
    assert parsed["strategic_foresight_ratio"] >= 0.30

    # Test Text output
    stdout_text_buf = io.StringIO()
    try:
        sys.stdout = stdout_text_buf
        ret_text_code = handle_executive_brief_cli([
            "--stakeholder", "Richard Argentieri",
            "--format", "text",
            "--db", db_path,
        ])
    finally:
        sys.stdout = old_stdout

    assert ret_text_code == 0
    text_val = stdout_text_buf.getvalue()
    assert "EXECUTIVE STRATEGIC BRIEFING: RICHARD ARGENTIERI" in text_val
    assert "PROACTIVE WHITE-SPACE EXPANSION PLAYS" in text_val
    assert "Commercial Asset Optimization & External SaaS Packaging" in text_val
