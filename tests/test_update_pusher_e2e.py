"""End-to-end test suite for proactive update pusher and Studio queue bridge.

Validates that staleness records, roster omissions, and transition signals are
pushed into SQLite validation_queue and exported as actionable Studio cards.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from unified_pipeline.temporal_reconciliation.receptive_watchlist_injector import TransitionSignal
from unified_pipeline.temporal_reconciliation.set_diff_reconciler import RoleRetractionCandidate
from unified_pipeline.temporal_reconciliation.staleness_auditor import StalenessRecord
from unified_pipeline.temporal_reconciliation.update_pusher import (
    ProactiveUpdatePusher,
    export_studio_queue,
)


def _init_test_graph_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_date TEXT,
            authority_rank INTEGER NOT NULL DEFAULT 5,
            source_posture TEXT NOT NULL DEFAULT 'INTERNAL',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            description TEXT,
            evidence_posture TEXT NOT NULL DEFAULT 'DOCUMENTED',
            confidence TEXT NOT NULL DEFAULT 'HIGH',
            materiality TEXT NOT NULL DEFAULT 'HIGH',
            validation_priority TEXT,
            validation_owner TEXT,
            hitL_required INTEGER NOT NULL DEFAULT 0,
            source_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE validation_queue (
            validation_id TEXT PRIMARY KEY CHECK (trim(validation_id)<>''),
            node_id TEXT REFERENCES node(node_id) ON UPDATE CASCADE ON DELETE RESTRICT,
            edge_id TEXT REFERENCES edge(edge_id) ON UPDATE CASCADE ON DELETE RESTRICT,
            claim_id TEXT,
            validation_question TEXT NOT NULL CHECK (trim(validation_question)<>''),
            owner TEXT NOT NULL CHECK (trim(owner)<>''),
            priority TEXT NOT NULL CHECK (priority IN ('P0','P1','P2','P3')),
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','closed')),
            resolution TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL CHECK (trim(created_at)<>'')
        );
        """
    )

    # Insert authentic entities and edges
    conn.execute("INSERT INTO node VALUES ('person_abhijit_mahishi', 'STAKEHOLDER', 'Abhijit Mahishi', 'PartnerAdvisory Tech Lead');")
    conn.execute("INSERT INTO node VALUES ('workstream_fcdb', 'WORKSTREAM', 'FCDB', 'Fault Code Database');")
    conn.execute("INSERT INTO source VALUES ('source_june_packet', 'STATUS', 'June Packet', '2026-06-29', 3, 'INTERNAL', '2026-06-29T12:00:00Z');")
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v36_person_abhijit_mahishi_leads_fcdb',
            'person_abhijit_mahishi', 'OWNS_OR_LEADS', 'workstream_fcdb',
            'Leads FCDB', 'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_june_packet', '2026-06-29T12:00:00Z', '2026-06-29T12:00:00Z', NULL
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_pusher.sqlite"
    _init_test_graph_db(db_file)
    return db_file


def test_push_stale_record_creates_validation_item_and_card(isolated_db: Path) -> None:
    pusher = ProactiveUpdatePusher(isolated_db)
    rec = StalenessRecord(
        edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        from_node_id="person_abhijit_mahishi",
        from_node_name="Abhijit Mahishi",
        edge_type="OWNS_OR_LEADS",
        to_node_id="workstream_fcdb",
        to_node_name="FCDB",
        source_id="source_june_packet",
        source_name="June Packet",
        source_date="2026-06-29",
        age_days=111,
        staleness_tier="P1",
        recommended_action="RETIRE_OR_REPLACE",
    )

    cards = pusher.push_stale_records([rec], reference_date="2026-10-18")
    assert len(cards) == 1
    card = cards[0]
    assert card.target_edge_id == "edge_v36_person_abhijit_mahishi_leads_fcdb"
    assert card.priority == "P1"
    assert len(card.options) == 2
    assert any(o.action_type == "RETIRE" for o in card.options)

    # Verify readback directly from SQLite
    conn = sqlite3.connect(f"file:{isolated_db.resolve().as_posix()}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT node_id, edge_id, priority, status FROM validation_queue;")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "person_abhijit_mahishi"
    assert rows[0][1] == "edge_v36_person_abhijit_mahishi_leads_fcdb"
    assert rows[0][2] == "P1"
    assert rows[0][3] == "open"


def test_idempotent_push_prevents_duplicate_validation_items(isolated_db: Path) -> None:
    pusher = ProactiveUpdatePusher(isolated_db)
    rec = StalenessRecord(
        edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        from_node_id="person_abhijit_mahishi",
        from_node_name="Abhijit Mahishi",
        edge_type="OWNS_OR_LEADS",
        to_node_id="workstream_fcdb",
        to_node_name="FCDB",
        source_id="source_june_packet",
        source_name="June Packet",
        source_date="2026-06-29",
        age_days=111,
        staleness_tier="P1",
        recommended_action="RETIRE_OR_REPLACE",
    )

    pusher.push_stale_records([rec], reference_date="2026-10-18")
    pusher.push_stale_records([rec], reference_date="2026-10-18")

    conn = sqlite3.connect(f"file:{isolated_db.resolve().as_posix()}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM validation_queue WHERE edge_id = 'edge_v36_person_abhijit_mahishi_leads_fcdb';")
    count = cur.fetchone()[0]
    conn.close()

    assert count == 1


def test_push_signal_with_replacement_option(isolated_db: Path) -> None:
    pusher = ProactiveUpdatePusher(isolated_db)
    sig = TransitionSignal(
        signal_id="sig_test_01",
        candidate_type="REPLACEMENT",
        supersedes_edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        retracted_entity_id="person_abhijit_mahishi",
        retracted_entity_name="Abhijit Mahishi",
        replacement_name="Bailey Morton",
        workstream_node_id="workstream_fcdb",
        workstream_name="FCDB",
        verbatim_quote="Bailey Morton has taken over for Abhijit Mahishi on FCDB",
        confidence_score=0.95,
        detection_method="EXPLICIT_REPLACEMENT",
    )

    cards = pusher.push_transition_signals([sig], reference_date="2026-10-18")
    assert len(cards) == 1
    card = cards[0]
    replace_opt = next((o for o in card.options if o.action_type == "REPLACE"), None)
    assert replace_opt is not None
    assert replace_opt.suggested_replacement == "Bailey Morton"


def test_export_studio_queue_json(isolated_db: Path, tmp_path: Path) -> None:
    pusher = ProactiveUpdatePusher(isolated_db)
    rec = StalenessRecord(
        edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        from_node_id="person_abhijit_mahishi",
        from_node_name="Abhijit Mahishi",
        edge_type="OWNS_OR_LEADS",
        to_node_id="workstream_fcdb",
        to_node_name="FCDB",
        source_id="source_june_packet",
        source_name="June Packet",
        source_date="2026-06-29",
        age_days=111,
        staleness_tier="P1",
        recommended_action="RETIRE_OR_REPLACE",
    )

    cards = pusher.push_stale_records([rec], reference_date="2026-10-18")
    target_json = tmp_path / "reports" / "stale_update_push_queue.json"
    written_path = export_studio_queue(cards, target_json)

    assert written_path.is_file()
    payload = json.loads(written_path.read_text(encoding="utf-8"))
    assert payload["total_cards"] == 1
    assert payload["cards"][0]["target_edge_id"] == "edge_v36_person_abhijit_mahishi_leads_fcdb"
