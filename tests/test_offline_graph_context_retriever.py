"""
tests/test_offline_graph_context_retriever.py

Unit tests for OfflineGraphContextRetriever:
1. Extraction of entities, active decisions, and lexical terms from SQLite fixture.
2. 100% C-19 quarantine exclusion (is_quarantined=1 excluded from context).
3. Graceful fallback on missing DB or uninitialized tables.
4. Sub-50ms bounded execution guarantee.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from dual_pipeline.query_core.offline_retriever import (
    GraphAssertionReference,
    GraphContextPacket,
    GraphEntityReference,
    OfflineGraphContextRetriever,
)


@pytest.fixture
def test_graph_db() -> Generator[Path, None, None]:
    """Creates a populated temporary SQLite graph database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Create node table
    cursor.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            organization TEXT,
            role TEXT,
            is_quarantined INTEGER DEFAULT 0
        )
    """)

    # Create assertion table
    cursor.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            predicate TEXT NOT NULL,
            subject_node_id TEXT NOT NULL,
            object_node_id TEXT,
            claim_text TEXT,
            is_quarantined INTEGER DEFAULT 0,
            observed_at TEXT
        )
    """)

    # Insert verified nodes
    cursor.executemany(
        """
        INSERT INTO node (node_id, canonical_name, display_name, node_type, organization, role, is_quarantined)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("project:nexus", "Project Nexus", "Project Nexus", "project", "Apex Global", "Digital Transformation", 0),
            ("person:amit_ayer", "Amit Ayer", "Amit Ayer", "person", "PartnerAdvisory", "Principal Architect", 0),
            ("person:dan_okray", "Drew Oakley", "Drew Oakley", "person", "PartnerAdvisory", "Managing Director", 0),
            ("system:turbines_hub", "Turbines Hub", "Turbines Hub", "system", "Apex Global", None, 0),
            ("workstream:phase1", "Phase 1 Core", "Phase 1 Core", "workstream", "Apex Global", None, 0),
            # Quarantined node (Rule C-19: Career leverage / shadow graph)
            ("person:internal_advisory_target", "Executive Target", "Executive Target", "person", "PartnerAdvisory", "VP", 1),
        ],
    )

    # Insert verified assertions
    cursor.executemany(
        """
        INSERT INTO assertion (assertion_id, predicate, subject_node_id, object_node_id, claim_text, is_quarantined, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "ast:001",
                "CONFIRMED_DECISION",
                "project:nexus",
                "system:turbines_hub",
                "Architecture migration to cloud API approved by leadership.",
                0,
                "09/15/2026",
            ),
            (
                "ast:002",
                "BLOCKING_RISK",
                "person:amit_ayer",
                "system:turbines_hub",
                "NERC CIP compliance verification requires sign-off before production cutover.",
                0,
                "09/18/2026",
            ),
            # Quarantined assertion (Rule C-19: Must NOT leak into synthesis prior context)
            (
                "ast:999",
                "INTERNAL_CAREER_LEVERAGE",
                "person:internal_advisory_target",
                None,
                "Executive positioning strategy for Q4 steering committee review.",
                1,
                "09/19/2026",
            ),
        ],
    )

    conn.commit()
    conn.close()

    yield db_path

    if db_path.exists():
        db_path.unlink()


def test_offline_retriever_basic_extraction(test_graph_db: Path) -> None:
    """Verifies that entities, decisions, and risks are extracted correctly."""
    retriever = OfflineGraphContextRetriever(default_graph_path=test_graph_db)
    packet = retriever.retrieve_meeting_prior_context(
        project_name="Project Nexus",
        attendees=["Amit Ayer", "Drew Oakley (PartnerAdvisory)"],
        graph_path=test_graph_db,
    )

    assert not packet.is_empty
    assert packet.duration_ms < 50.0  # Bounded execution invariant

    # Check entities
    node_ids = {e.node_id for e in packet.entities}
    assert "project:nexus" in node_ids
    assert "person:amit_ayer" in node_ids
    assert "person:dan_okray" in node_ids

    # Check decisions and risks
    predicates = {d.predicate for d in packet.decisions}
    assert "CONFIRMED_DECISION" in predicates

    risk_preds = {r.predicate for r in packet.risks}
    assert "BLOCKING_RISK" in risk_preds

    # Check lexical terms
    assert "Project Nexus" in packet.lexical_terms
    assert "Turbines Hub" in packet.lexical_terms

    # Check formatted markdown string
    assert "### Known Graph Entities & Roster Grounding" in packet.formatted_prior_context
    assert "### Active Predecessor Decisions & Commitments (Rule C-16)" in packet.formatted_prior_context
    assert "### In-Flight Risks & Dependencies" in packet.formatted_prior_context


def test_offline_retriever_c19_career_firewall_quarantine(test_graph_db: Path) -> None:
    """Verifies 100% quarantine exclusion under Rule C-19 (no shadow graph leakage)."""
    retriever = OfflineGraphContextRetriever(default_graph_path=test_graph_db)
    packet = retriever.retrieve_meeting_prior_context(
        project_name="Project Nexus",
        attendees=["Executive Target"],
        graph_path=test_graph_db,
    )

    # Quarantined person must NOT be in entities
    entity_ids = {e.node_id for e in packet.entities}
    assert "person:internal_advisory_target" not in entity_ids

    # Quarantined assertion must NOT be in decisions or risks
    decision_ids = {d.assertion_id for d in packet.decisions}
    risk_ids = {r.assertion_id for r in packet.risks}
    assert "ast:999" not in decision_ids
    assert "ast:999" not in risk_ids
    assert "INTERNAL_CAREER_LEVERAGE" not in packet.formatted_prior_context
    assert "Executive Target" not in packet.formatted_prior_context


def test_offline_retriever_graceful_fallback_on_missing_db() -> None:
    """Verifies fail-safe fallback when graph database does not exist."""
    missing_path = Path("/tmp/non_existent_graph_db_xyz123.db")
    retriever = OfflineGraphContextRetriever(default_graph_path=missing_path)

    packet = retriever.retrieve_meeting_prior_context(
        project_name="Unknown Project",
        attendees=["Alice", "Bob"],
        graph_path=missing_path,
    )

    assert packet.is_empty
    assert len(packet.entities) == 0
    assert len(packet.decisions) == 0
    assert len(packet.risks) == 0
    assert packet.formatted_prior_context == ""
    assert packet.duration_ms < 50.0


def test_offline_retriever_graceful_fallback_on_empty_db() -> None:
    """Verifies fallback when database exists but lacks expected schema tables."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE dummy (id INTEGER)")
    conn.commit()
    conn.close()

    try:
        retriever = OfflineGraphContextRetriever(default_graph_path=db_path)
        packet = retriever.retrieve_meeting_prior_context(
            project_name="Test",
            attendees=["Dave"],
            graph_path=db_path,
        )
        assert packet.is_empty
    finally:
        if db_path.exists():
            db_path.unlink()


def test_offline_retriever_packet_serialization() -> None:
    """Tests to_dict serialization of GraphContextPacket."""
    packet = GraphContextPacket(
        entities=[
            GraphEntityReference(
                node_id="person:test",
                canonical_name="Test User",
                display_name="Test User",
                node_type="person",
                organization="TestOrg",
                role="Architect",
            )
        ],
        decisions=[
            GraphAssertionReference(
                assertion_id="ast:1",
                predicate="DECISION_APPROVED",
                subject_node_id="person:test",
                claim_text="Approved tech spec.",
                observed_at="09/20/2026",
            )
        ],
        lexical_terms=["Test User", "Kubernetes"],
        duration_ms=1.2,
    )

    d = packet.to_dict()
    assert d["is_empty"] is False
    assert len(d["entities"]) == 1
    assert d["entities"][0]["node_id"] == "person:test"
    assert len(d["decisions"]) == 1
    assert d["decisions"][0]["predicate"] == "DECISION_APPROVED"
    assert "Kubernetes" in d["lexical_terms"]


def test_dynamic_project_traversal_and_transcript_spotting() -> None:
    """Verifies that project topologies are traversed to identify resources and transcript names are spotted."""
    retriever = OfflineGraphContextRetriever()
    packet = retriever.retrieve_meeting_prior_context(
        project_name="project_lens",
        attendees=["Mitch Simmons"],
        transcript_text="[00:01:00] Mitch: We spoke with Mandy and Reid regarding the deliverables.",
    )
    assert not packet.is_empty
    names = {e.canonical_name for e in packet.entities}
    # Traversed from LENS workstream edges
    assert "Alex Sanchez" in names
    assert "Tony Kablan" in names
    # Spotted from dialogue turn text
    assert "harper Hoffman" in names
    assert "Reid Schenk" in names
    assert packet.duration_ms < 50.0
