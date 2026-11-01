"""
tests/end_to_end/test_copilot_engagement_wave1_e2e.py

End-to-End acceptance test for Wave 1: verifies isolated runtime execution,
graph provisioning, boundary leakage reproduction, full provenance binding record
generation, and disk-persisted provenance ledger integrity.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
)
from tests.fixtures.copilot_engagement import (
    CopilotProvenanceRecord,
    compute_answer_sha256,
    create_provenance_record,
    load_incident_answers,
    load_session_snapshot,
    provision_isolated_sqlite_graph,
)


def _load_topology_from_db(db_path: Path) -> SimpleNamespace:
    """Reads nodes and assertions from the isolated SQLite DB into a topology object."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    nodes: Dict[str, Any] = {}
    assertions: Dict[str, Any] = {}
    forward_adjacency: Dict[str, List[str]] = {}

    with conn:
        for row in conn.execute("SELECT node_id, canonical_name, node_type, engagement_id, aliases_json FROM knowledge_nodes;"):
            nid, name, ntype, eng_id, aliases_json = row
            nodes[nid] = {
                "canonical_name": name,
                "node_type": ntype,
                "engagement_id": eng_id,
                "aliases": json.loads(aliases_json) if aliases_json else [],
            }
        for row in conn.execute("SELECT assertion_id, subject_node_id, predicate, object_node_id, claim_text, project_scope, engagement_id FROM assertions;"):
            asrt_id, sub, pred, obj, claim, scope, eng_id = row
            assertions[asrt_id] = {
                "assertion_id": asrt_id,
                "subject_node_id": sub,
                "predicate": pred,
                "object_node_id": obj,
                "claim_text": claim,
                "project_scope": scope,
                "engagement_id": eng_id,
            }
            forward_adjacency.setdefault(sub, []).append(asrt_id)

    return SimpleNamespace(
        nodes=nodes,
        assertions=assertions,
        forward_adjacency=forward_adjacency,
    )


def test_copilot_engagement_e2e_reproduction_and_ledger(tmp_path: Path) -> None:
    """
    E2E test: provisions an isolated SQLite graph, reproduces cross-engagement
    boundary leakage, binds full provenance, and verifies ledger persistence.
    """
    # 1. Provision isolated SQLite graph
    db_file = tmp_path / "isolated_copilot_graph.db"
    provision_isolated_sqlite_graph(db_file)
    assert db_file.is_file(), "Isolated DB file must exist"

    topology = _load_topology_from_db(db_file)
    assert len(topology.nodes) >= 10
    assert len(topology.assertions) >= 7

    # 2. Simulate active recorder session registration
    sess_dir = tmp_path / ".session_registry"
    sess_dir.mkdir(parents=True, exist_ok=True)
    session_id = "test-e2e-session-uuid-001"
    session_data = {
        "session_id": session_id,
        "meeting_slug": "Engineering_Jarvis_Kaleena_PartnerAdvisory",
        "ws_port": 8765,
        "created_at": 1791399078.0,
        "last_heartbeat": 1791399078.0,
    }
    (sess_dir / f"{session_id}.json").write_text(json.dumps(session_data), encoding="utf-8")

    # 3. Initialize Copilot retriever on isolated topology
    retriever = CopilotGraphRetriever(
        topology=topology,
        attendees=[{"name": "Shared Engineer"}],
        graph_sha256="fake_isolated_graph_sha256",
        generation=1,
    )

    # 4. Reproduce boundary leakage in the isolated environment
    leakage_query = "What does Spatial File Upload depend on?"
    retrieved = retriever.retrieve_structured_assertions(leakage_query)
    retrieved_ids = [a["assertion_id"] for a in retrieved]
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" in retrieved_ids, (
        "E2E Reproduction: LENS assertion 'Spatial File Upload depends on JIRA' leaked into query results"
    )

    # 5. Build and verify CopilotProvenanceRecord for the reproduced response
    displayed_answer = "Spatial File Upload depends on JIRA."
    prov_record = create_provenance_record(
        input_request=leakage_query,
        selected_session_id=session_id,
        engagement_id="project:jarvis",
        source_assertion_ids=retrieved_ids,
        displayed_answer=displayed_answer,
        route_identity="cli",
        missing_historical_binding=False,
    )
    assert prov_record.answer_sha256 == compute_answer_sha256(displayed_answer)
    assert prov_record.engagement_id == "project:jarvis"
    assert "assertion-b7cd2f78b3a1c4995bd4cfb1ee31" in prov_record.source_assertion_ids

    # 6. Persist to ledger and verify read-back equality
    ledger_path = tmp_path / "provenance_ledger.jsonl"
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(prov_record.to_dict()) + "\n")

    lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["record_id"] == prov_record.record_id
    assert lines[0]["answer_sha256"] == prov_record.answer_sha256


def test_copilot_engagement_e2e_incident_corpus_ledger(tmp_path: Path) -> None:
    """
    E2E test: verifies that all 8 incident answers and the missing narrative
    can be ingested into an append-only provenance ledger with verified hashes.
    """
    incident_answers = load_incident_answers()
    sess_snapshot = load_session_snapshot()
    session_id = sess_snapshot["session_id"]
    ledger_path = tmp_path / "incident_provenance_ledger.jsonl"

    records: List[CopilotProvenanceRecord] = []
    for item in incident_answers:
        resp = item["response"]
        rec = create_provenance_record(
            input_request=f"copilot intent {item.get('intent', 'unknown')}",
            selected_session_id=session_id,
            engagement_id="project:jarvis",
            source_assertion_ids=item.get("cited_assertion_ids", []),
            displayed_answer=resp,
            route_identity="cli",
            missing_historical_binding=False,
        )
        records.append(rec)
        with ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")

    # Ingest the explicit missing narrative record
    missing_prose = "At Apex Global, SecOps review and enterprise identity provisioning typically have a 2-week turnaround."
    unbound_rec = create_provenance_record(
        input_request="Historical three-issue pasted text",
        selected_session_id=session_id,
        engagement_id="project:jarvis",
        source_assertion_ids=[],
        displayed_answer=missing_prose,
        route_identity="unbound_cli_or_host",
        missing_historical_binding=True,
        unresolved_attribution_note="Unbound from the eight snapshotted JSONL records",
    )
    records.append(unbound_rec)
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(unbound_rec.to_dict()) + "\n")

    read_back = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(read_back) == 9
    assert read_back[-1]["missing_historical_binding"] is True
    assert read_back[-1]["unresolved_attribution_note"] is not None
