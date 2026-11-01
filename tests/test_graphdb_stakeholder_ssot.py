"""
tests/test_graphdb_stakeholder_ssot.py

Wave 4 Acceptance Gate: GraphDB Stakeholder SSOT & Cross-Output Consistency Validator.
Validates that stakeholder titles, roles, and identities are consistent across Outputs 1, 2, and 3,
and that discrepancies (e.g. Lead Architect vs Executive Architect) fail closed.
"""
from pathlib import Path

import pytest

from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderInconsistencyError,
    StakeholderResolutionValidator,
    sync_stakeholder_to_graph,
)
from storage.connection import create_sqlite_conn


def test_consistent_deliverables_pass_validation():
    validator = StakeholderResolutionValidator()

    o1_text = "| Amit Ayer | PartnerAdvisory | Principal Architect & Technology Strategist |"
    o2_text = "### Key Attendees\n- **Amit Ayer** (Principal Architect & Technology Strategist)\n- **harper Hoffman** (Product Manager)"
    o3_json = {
        "source_metadata": {
            "roster": [
                {"name": "Amit Ayer", "role": "Principal Architect & Technology Strategist"},
                {"name": "harper Hoffman", "role": "Product Manager"},
            ]
        },
        "entities": [
            {"canonical_name": "Amit Ayer", "role": "Principal Architect & Technology Strategist"},
        ]
    }

    report = validator.validate_deliverables(
        output_1_text=o1_text,
        output_2_text=o2_text,
        output_3_payload=o3_json,
        fail_closed=True,
    )
    assert report.is_consistent is True
    assert len(report.discrepancies) == 0
    assert "Amit Ayer" in report.stakeholders_checked


def test_inconsistent_roles_fail_closed_with_error():
    validator = StakeholderResolutionValidator()

    # User's exact prompt example:
    # Output 2 has Amit Ayer = Lead Architect, Output 3 has Amit Ayer = Executive Architect
    o1_text = "| Amit Ayer | PartnerAdvisory | Principal Architect & Technology Strategist |"
    o2_text = "### Key Attendees\n- **Amit Ayer** (Lead Architect)"
    o3_json = {
        "source_metadata": {
            "roster": [
                {"name": "Amit Ayer", "role": "Executive Architect"},
            ]
        }
    }

    with pytest.raises(StakeholderInconsistencyError) as exc_info:
        validator.validate_deliverables(
            output_1_text=o1_text,
            output_2_text=o2_text,
            output_3_payload=o3_json,
            fail_closed=True,
        )

    err = str(exc_info.value)
    assert "conflicting roles across deliverables" in err
    assert "Amit Ayer" in err


def test_graphdb_stakeholder_sync(tmp_path: Path):
    test_db = tmp_path / "test_graph.sqlite"
    conn = create_sqlite_conn(test_db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    # 1. Insert new stakeholder
    res = sync_stakeholder_to_graph(
        person_name="Marcus Vance",
        role="Principal Cloud Architect",
        org="Apex Global Enterprises",
        db_path=test_db,
    )
    assert res is True

    # Verify insertion
    conn2 = create_sqlite_conn(test_db)
    cur2 = conn2.cursor()
    cur2.execute("SELECT canonical_name, description FROM node WHERE canonical_name = 'Marcus Vance'")
    row = cur2.fetchone()
    assert row is not None
    assert row[0] == "Marcus Vance"
    assert "Principal Cloud Architect" in row[1]

    # 2. Update existing stakeholder role
    res_update = sync_stakeholder_to_graph(
        person_name="Marcus Vance",
        role="VP of Engineering",
        org="Apex Global Enterprises",
        db_path=test_db,
    )
    assert res_update is True
    cur2.execute("SELECT description FROM node WHERE canonical_name = 'Marcus Vance'")
    row2 = cur2.fetchone()
    assert "VP of Engineering" in row2[0]
    conn2.close()
