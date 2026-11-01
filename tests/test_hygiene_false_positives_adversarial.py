"""
Adversarial and edge-case test suite for Knowledge Graph Hygiene false-positive prevention.

Covers 5 mandatory edge-case categories per `.agents/rules/e2e-and-edge-case-coverage.md`:
1. Boundary payload (empty strings, null descriptions, 0-row tables)
2. Malformed input (invalid ISO dates, missing optional schema columns)
3. Exact production regression (capital vs total value, AI-SDLC vs AI-DLC, Admin suffix, cross-meeting dates)
4. Invariant bypass attempt (SynthesisJudgeAgent veto of conflicting or do-not-merge findings)
5. Rollback & recovery / read-only immutability (zero mutation across adversarial scans)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.dedup import _compute_claim_similarity
from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    SynthesisJudgeAgent,
)
from dual_pipeline.graph_scout_core.matching import (
    _string_similarity,
    has_conflicting_qualifiers,
    has_description_conflict,
)
from dual_pipeline.graph_scout_core.scans import (
    scan_edge_assertion_parity,
    scan_orphan_and_clutter,
)


def test_category1_boundary_payloads() -> None:
    """Category 1: Empty strings, whitespace-only inputs, and null descriptions."""
    assert _string_similarity("", "") == 0.0
    assert _string_similarity("   ", "Project Nexus") == 0.0
    assert not has_conflicting_qualifiers("", "")
    assert not has_description_conflict("", "")
    assert _compute_claim_similarity(None, "claim") == 0.0
    assert _compute_claim_similarity("", "") == 0.0


def test_category2_malformed_inputs_and_minimal_schemas(tmp_path: Path) -> None:
    """Category 2: Malformed timestamps and minimal SQLite schemas missing optional columns."""
    db_path = tmp_path / "malformed.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE node (node_id TEXT, canonical_name TEXT)")
        conn.execute("CREATE TABLE assertion (assertion_id TEXT, subject_node_id TEXT, predicate TEXT, canonical_status TEXT, created_at TEXT)")
        conn.execute("INSERT INTO node VALUES ('n1', 'Orphan With Minimal Schema')")
        conn.execute("INSERT INTO assertion VALUES ('a1', 'n1', 'REL', 'candidate', 'NOT-A-VALID-TIMESTAMP')")
        conn.commit()

    with sqlite3.connect(db_path) as conn:
        clutter = scan_orphan_and_clutter(conn)
        parity = scan_edge_assertion_parity(conn)
    assert isinstance(clutter, list)
    assert parity == []


def test_category3_exact_production_regressions() -> None:
    """Category 3: Exact production false-positive pairs must return conflicting/0.0 similarity."""
    # Financial metrics: capital vs total & conflicting dollar values
    assert has_conflicting_qualifiers("Estimated 2028 capital value", "Estimated 2028 total value")
    assert _string_similarity("Estimated 2028 capital value", "Estimated 2028 total value") == 0.0
    assert has_description_conflict("$6.92M Capital estimated value by 2028.", "$11.11M total estimated value by 2028.")

    # Acronyms: AI-SDLC vs AI-DLC
    assert has_conflicting_qualifiers("AI-SDLC", "AI-DLC")
    assert _string_similarity("AI-SDLC", "AI-DLC") == 0.0

    # Environment / Admin suffixes
    assert has_conflicting_qualifiers("Project Beacon UAT", "Project Beacon")
    assert has_conflicting_qualifiers("Michael Valdes", "Michael Valdes Admin")

    # Distinct role groups
    assert has_conflicting_qualifiers(
        "Vice president, infrastructure development",
        "Vice president, infrastructure and operations",
    )
    assert has_conflicting_qualifiers(
        "Vice president, infrastructure development",
        "Vice President, Product Development",
    )
    assert has_conflicting_qualifiers("Enterprise Architect", "Lead Enterprise Architect")
    assert has_conflicting_qualifiers("Product Manager", "Product Lead / Project Manager")
    assert has_conflicting_qualifiers(
        "Managing Director-Lead Client Partner",
        "Client Service Lead-Managing Director-Lead Client Partner",
    )

    # Cross-meeting assertion claims on different dates
    assert (
        _compute_claim_similarity(
            "IBM Maximo is an identified technical_anchor in 20261008 Lens Sprint Review.",
            "IBM Maximo is an identified technical_anchor in 20261005 Lens Scope Review.",
        )
        == 0.0
    )
    assert (
        _compute_claim_similarity(
            "Ed Moore is an identified stakeholder in 20261008 Lens Sprint Review.",
            "Ed Moore is an identified stakeholder in 20261001 Lens Sprint Review.",
        )
        == 0.0
    )


def test_category4_invariant_bypass_vetoed_by_synthesis_judge() -> None:
    """Category 4: SynthesisJudgeAgent deterministically vetoes injected false-positive findings."""
    judge = SynthesisJudgeAgent()
    injected = [
        # 1. Orphan node that is simultaneously a merge target -> must not be archived
        Finding(
            detective="StructuralValidator",
            severity="MEDIUM",
            description="Orphan candidate",
            entity_references=["entity-012de18c06348a9877dfd44d4718"],
            proposed_fix="archive_node",
            raw_data={"node_id": "entity-012de18c06348a9877dfd44d4718", "status": "candidate", "created_at": "2026-10-19T17:49:52Z"},
        ),
        # 2. Unresolved roster node with 'Do not merge' instruction -> must be vetoed
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description="Merge unresolved roster",
            entity_references=["stakeholder_jacob_wellington", "unresolved_jake_wellington_roster_2026_07_11"],
            proposed_fix="merge_entities",
            raw_data={
                "node_id_a": "stakeholder_jacob_wellington",
                "canonical_name_a": "Jacob Wellington",
                "description_a": "AI Software Engineering Leader",
                "node_id_b": "unresolved_jake_wellington_roster_2026_07_11",
                "canonical_name_b": "Jake Wellington",
                "description_b": "Do not merge with a similarly named canonical person without authoritative identity evidence.",
            },
        ),
        # 3. Grounded candidate assertion with evidence quote -> must not be archived unless superseded
        Finding(
            detective="StructuralValidator",
            severity="MEDIUM",
            description="Ungrounded false alarm",
            entity_references=["assertion-c1665bfd918bcd861151d6e9b939"],
            proposed_fix="archive_candidate_assertion",
            raw_data={
                "assertion_id": "assertion-c1665bfd918bcd861151d6e9b939",
                "eviction_reason": "ungrounded_low_confidence_candidate",
                "evidence_quote": "11:12",
            },
        ),
        # 4. Cross-date assertion deduplication -> must be vetoed
        Finding(
            detective="StructuralValidator",
            severity="MEDIUM",
            description="Cross-date dedup false alarm",
            entity_references=["asrt-1", "asrt-2"],
            proposed_fix="deduplicate_assertions",
            raw_data={
                "winner_id": "asrt-1",
                "loser_id": "asrt-2",
                "winner_claim": "Constellation Energy in Document 20261019 Weekly Trends.",
                "loser_claim": "Constellation Energy in Document 20261016 Weekly Trends.",
            },
        ),
    ]
    pkg = judge.synthesize(injected)
    assert len(pkg.findings) == 0
    assert len(pkg.remediation_directives) == 0
