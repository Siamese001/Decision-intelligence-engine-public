"""
tests/test_offline_retriever_attribution.py

Unit test suite for Wave 4: Query Traversal & Offline Context Retriever Partitioning.
Verifies:
1. Prior context query for project_lens extracts Jen Pan and Alex Sanchez with 0 Sherpa entities or assertions.
2. Prior context query for project_beacon extracts Clay Palumbo and Lillian Le with 0 Jen Pan assertions.
3. Formatted markdown strictly fences entities under workstream-specific headers (#### Project LENS, #### Project Beacon).
4. GraphEntityReference carries authoritative associated_workstreams.
5. In-dialogue entity spotting respects workstream boundaries during scoped retrieval.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.query_core.offline_models import GraphContextPacket
from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever
from dual_pipeline.query_core.traversal_helper import spot_transcript_entities
from storage.database import sqlite3


@pytest.fixture(scope="module")
def retriever():
    graph_path = Path("graph/nee_graph.sqlite")
    assert graph_path.is_file(), "Active graph file graph/nee_graph.sqlite must exist"
    return OfflineGraphContextRetriever(default_graph_path=graph_path)


def test_lens_prior_context_extraction(retriever: OfflineGraphContextRetriever):
    """Querying offline prior context for project_lens extracts Jen Pan and Alex Sanchez, with 0 Sherpa entities or assertions."""
    pkt: GraphContextPacket = retriever.retrieve_meeting_prior_context(project_name="project_lens")

    entity_ids = [e.node_id for e in pkt.entities]
    assert "person_jen_pan" in entity_ids, "person_jen_pan must be present in Project LENS prior context"
    assert "person_alex_sanchez" in entity_ids, "person_alex_sanchez must be present in Project LENS prior context"

    # Zero Sherpa entities
    sherpa_entities = [eid for eid in entity_ids if "sherpa" in eid.lower() or eid in ("person_clay_palumbo", "person_lillian", "person_andy_k")]
    assert len(sherpa_entities) == 0, f"Found Sherpa entities in LENS query: {sherpa_entities}"

    # Zero Sherpa assertions
    all_assertions = pkt.decisions + pkt.risks
    sherpa_assertions = [
        a.assertion_id for a in all_assertions
        if "sherpa" in a.assertion_id.lower()
        or (a.subject_node_id and "sherpa" in a.subject_node_id.lower())
        or (a.object_node_id and "sherpa" in a.object_node_id.lower())
    ]
    assert len(sherpa_assertions) == 0, f"Found Sherpa assertions in LENS query: {sherpa_assertions}"

    # Markdown partitioning check
    assert "#### Project LENS (org:nea)" in pkt.formatted_prior_context
    assert "person_jen_pan" in pkt.formatted_prior_context
    assert "#### Project Beacon" not in pkt.formatted_prior_context


def test_sherpa_prior_context_extraction(retriever: OfflineGraphContextRetriever):
    """Querying offline prior context for project_beacon extracts Clay Palumbo and Lillian Le, with 0 Jen Pan assertions."""
    pkt: GraphContextPacket = retriever.retrieve_meeting_prior_context(project_name="project_beacon")

    entity_ids = [e.node_id for e in pkt.entities]
    assert "person_clay_palumbo" in entity_ids, "person_clay_palumbo must be present in Project Beacon prior context"
    assert "person_lillian" in entity_ids, "person_lillian must be present in Project Beacon prior context"

    # Zero Jen Pan or LENS entities
    assert "person_jen_pan" not in entity_ids, "person_jen_pan must NOT be present in Project Beacon prior context"
    assert "workstream_lens" not in entity_ids, "workstream_lens must NOT be present in Project Beacon prior context"

    # Zero Jen Pan assertions
    all_assertions = pkt.decisions + pkt.risks
    jen_pan_assertions = [
        a.assertion_id for a in all_assertions
        if "jen_pan" in a.assertion_id.lower()
        or a.subject_node_id == "person_jen_pan"
        or a.object_node_id == "person_jen_pan"
    ]
    assert len(jen_pan_assertions) == 0, f"Found Jen Pan assertions in Sherpa query: {jen_pan_assertions}"

    # Markdown partitioning check
    assert "#### Project Beacon (org:nee_it)" in pkt.formatted_prior_context
    assert "#### Project LENS" not in pkt.formatted_prior_context
    assert "person_jen_pan" not in pkt.formatted_prior_context


def test_associated_workstreams_annotation(retriever: OfflineGraphContextRetriever):
    """Verify GraphEntityReference objects carry authoritative associated_workstreams."""
    pkt_lens = retriever.retrieve_meeting_prior_context(project_name="project_lens")
    jen_ent = next((e for e in pkt_lens.entities if e.node_id == "person_jen_pan"), None)
    assert jen_ent is not None
    assert "workstream_lens" in jen_ent.associated_workstreams

    pkt_sherpa = retriever.retrieve_meeting_prior_context(project_name="project_beacon")
    clay_ent = next((e for e in pkt_sherpa.entities if e.node_id == "person_clay_palumbo"), None)
    assert clay_ent is not None
    assert "workstream_project_beacon" in clay_ent.associated_workstreams


def test_spot_transcript_entities_with_scoping():
    """Verify in-dialogue entity spotting filters out disjoint workstream entities when scoped."""
    conn = sqlite3.connect("graph/nee_graph.sqlite")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    transcript_text = "Cameron Mercer said Jen Pan is on the Lens team, while Clay Palumbo and Lillian are in Sherpa."

    # Scoped to LENS
    lens_entities, _ = spot_transcript_entities(
        cursor=cursor,
        transcript_text=transcript_text,
        has_quarantine=False,
        scoped_workstreams=["workstream_lens"],
    )
    lens_ids = [e.node_id for e in lens_entities]
    assert "person_jen_pan" in lens_ids
    assert "person_clay_palumbo" not in lens_ids
    assert "person_lillian" not in lens_ids

    # Scoped to Sherpa
    sherpa_entities, _ = spot_transcript_entities(
        cursor=cursor,
        transcript_text=transcript_text,
        has_quarantine=False,
        scoped_workstreams=["workstream_project_beacon"],
    )
    sherpa_ids = [e.node_id for e in sherpa_entities]
    assert "person_jen_pan" not in sherpa_ids
    assert "person_clay_palumbo" in sherpa_ids or "person_lillian" in sherpa_ids

    conn.close()
