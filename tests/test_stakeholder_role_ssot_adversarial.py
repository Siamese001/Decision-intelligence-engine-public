"""
tests/test_stakeholder_role_ssot_adversarial.py

Adversarial and edge-case test suite for Wave 2 Stakeholder Role Extraction,
UWG Persistence, and Graph Data Hygiene.
Covers 5 edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty descriptions, null properties, missing subject node auto-creation)
2. Malformed input (prepositional titles, compound VP & CIO titles, semicolon clauses, contaminated seeds)
3. Concurrency & race (multi-threaded UWG persist_validated_role with assertion_evidence joins)
4. Invariant bypass attempt (OWNS_OR_LEADS workstream-as-title injection, static directory override attempt)
5. Exact production regression (Eduardo More, Parker Vance, Andrew Hanson, Calvin Gilmartin)
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import Dict

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import PreSynthesisEntityResolver
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    _extract_role_text,
    _parse_entity_org_and_role,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn
from tests.end_to_end.test_stakeholder_role_ssot_e2e import _seed_production_like_sandbox


# ==============================================================================
# Category 1: Boundary payload
# ==============================================================================

def test_boundary_empty_descriptions_and_missing_subject_node_creation(tmp_path: Path) -> None:
    """Verifies empty/null descriptions default safely and missing person nodes are created with valid node_type."""
    org, role = _parse_entity_org_and_role(None, {})
    assert org == "Client / Partner"
    assert role == "Participant"

    org2, role2 = _parse_entity_org_and_role("   ", {"role": ""})
    assert role2 == "Participant"

    db_path = _seed_production_like_sandbox(tmp_path / "graph.sqlite")
    # Persist role for a brand-new stakeholder not yet in node table
    receipt = persist_validated_role(
        db_path,
        "Brand New Stakeholder",
        "VP of Engineering & Operations",
    )
    assert receipt["status"] == "PERSISTED_SSOT"

    conn = create_sqlite_conn(db_path, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT node_type, description FROM node WHERE node_id = ?",
        (receipt["subject_node_id"],),
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == "stakeholder"
    assert "VP of Engineering & Operations" in row[1]
    conn.close()


# ==============================================================================
# Category 2: Malformed input & complex title syntax
# ==============================================================================

@pytest.mark.parametrize(
    ("raw_desc", "expected_role"),
    [
        (
            "Director of Operational Technology; Apex Global Enterprises (NEA).",
            "Director of Operational Technology",
        ),
        (
            "VP & CIO; Executive stakeholder in IT.",
            "VP & CIO",
        ),
        (
            "Merged into node person_alex (Alex Corrow, Director of Operational Technology)",
            "Director of Operational Technology",
        ),
        (
            "Affiliation label(s): NEA. Source roles: Atlas Lead / Rule Specialist & VP / Executive Sponsor.",
            "Atlas Lead / Rule Specialist",
        ),
        (
            "UX Researcher; Project Beacon team member.",
            "UX Researcher",
        ),
        (
            "Senior Director, Trading Risk & Analytics; Commercial stakeholder.",
            "Senior Director, Trading Risk & Analytics",
        ),
    ],
)
def test_complex_and_contaminated_description_role_parsing(
    raw_desc: str, expected_role: str
) -> None:
    """Verifies prepositional, compound, merged, and contaminated descriptions parse cleanly."""
    _org, role = _parse_entity_org_and_role(raw_desc, {})
    assert role == expected_role


# ==============================================================================
# Category 3: Concurrency & race
# ==============================================================================

def test_concurrent_uwg_role_persistence_with_evidence_binding(tmp_path: Path) -> None:
    """Verifies 8 concurrent UWG role persistence workers serialize cleanly and all bind assertion_evidence."""
    db_path = _seed_production_like_sandbox(tmp_path / "graph.sqlite")

    def _worker(idx: int) -> Dict[str, object]:
        return persist_validated_role(
            db_path,
            f"Concurrent Leader {idx}",
            f"Director of Platform Engineering {idx}",
            operator_id=f"worker_{idx}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker, i) for i in range(8)]
        receipts = [f.result(timeout=15) for f in futures]

    assert len(receipts) == 8
    conn = create_sqlite_conn(db_path, readonly=True)
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchone()[0] == "ok"

    for r in receipts:
        aid = r["persisted_assertion_id"]
        cur.execute(
            "SELECT COUNT(*) FROM assertion_evidence WHERE assertion_id = ? AND evidence_relationship = 'supports'",
            (aid,),
        )
        assert cur.fetchone()[0] == 1
    conn.close()


# ==============================================================================
# Category 4: Invariant bypass attempt
# ==============================================================================

def test_workstream_and_deliverable_targets_never_leak_into_roles() -> None:
    """Verifies non-role object node types cannot leak into personal job titles."""
    for bad_type in ("workstream", "deliverable", "program", "project", "sow", "org", "card"):
        extracted = _extract_role_text(
            obj_lit="Lens-Pgd-Stakeholders",
            scope="Parker Vance OWNS_OR_LEADS Lens-Pgd-Stakeholders.",
            o_type=bad_type,
            o_name="Lens-Pgd-Stakeholders",
        )
        assert extracted is None, f"Object type {bad_type} leaked as role: {extracted}"


def test_static_directory_cannot_override_graphdb_ssot_role(tmp_path: Path) -> None:
    """Verifies that an updated GraphDB role takes strict precedence over static directory priors."""
    db_path = _seed_production_like_sandbox(tmp_path / "graph.sqlite")
    persist_validated_role(
        db_path,
        "Eduardo More",
        "Principal Atlas Rule Architect",
        operator_id="ssot_precedence_test",
    )

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path, runtime_root=tmp_path)
    resolved = resolver.resolve("Eduardo More", project_name="project_lens")
    assert resolved is not None
    assert resolved.role == "Principal Atlas Rule Architect"


# ==============================================================================
# Category 5: Exact production regression
# ==============================================================================

def test_production_regression_eduardo_more_and_anthony_funk(tmp_path: Path) -> None:
    """Pins exact production regression cases for Eduardo More, Parker Vance, Andrew Hanson, and Calvin Gilmartin."""
    _seed_production_like_sandbox(tmp_path / "graph.sqlite")
    resolver = PreSynthesisEntityResolver(repo_root=tmp_path, runtime_root=tmp_path)

    cases = {
        "Eduardo More": "Atlas Lead / Rule Specialist",
        "Ed More": "Atlas Lead / Rule Specialist",
        "Parker Vance": "Director Strategic Initiatives, Op Excellence",
        "Andrew Hanson": "AI/ML Director",
        "Calvin Gilmartin": "Senior Director, Trading Risk & Analytics",
    }
    for name, expected_title in cases.items():
        ent = resolver.resolve(name, project_name="project_lens")
        assert ent is not None, f"Failed to resolve {name}"
        assert ent.role == expected_title, f"For {name}: expected '{expected_title}', got '{ent.role}'"
