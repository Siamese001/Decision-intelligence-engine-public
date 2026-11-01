"""
End-to-End tests for Wave 6 (CAT-5 P0 & P1):
1. _prepare_fingerprint_replacement and _restore_fingerprint_replacement detach validation_queue before deleting assertion (no FK violation) and rebind across target_id, assertion_id, and claim_id schemas.
2. _evict_fts_and_resolve_validation_queue and _retire_omitted_source_assertions evict FTS rows and resolve open validation_queue items.
3. _repoint_edges_for_source and _repoint_claims_for_source repoint colliding child map/matrix rows to surviving winner IDs instead of deleting them.
4. _retire_unreferenced_retired_sources checks edge.source_id and claim.source_id before retiring and cleans up search_fts.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_writer_core.helpers import (
    _prepare_fingerprint_replacement,
    _restore_fingerprint_replacement,
    _retire_omitted_source_assertions,
    _sync_search_fts_row,
)
from tools.tier2_dedup_core.hygiene_promotion import (
    _repoint_claims_for_source,
    _repoint_edges_for_source,
    _retire_unreferenced_retired_sources,
)


def test_fingerprint_replacement_detaches_and_restores_validation_queue_with_fk_enabled(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_fk.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER,
            created_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT
        );
        CREATE TABLE search_fts (
            rowid INTEGER,
            title TEXT,
            body TEXT,
            object_type TEXT,
            object_id TEXT
        );
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            target_id TEXT,
            assertion_id TEXT,
            claim_id TEXT,
            status TEXT DEFAULT 'open',
            FOREIGN KEY(assertion_id) REFERENCES assertion(assertion_id)
        );
        """
    )
    conn.execute("INSERT INTO assertion (assertion_id) VALUES ('asrt-old')")
    conn.execute(
        "INSERT INTO validation_queue VALUES ('vq-1', 'asrt-old', 'asrt-old', 'asrt-old', 'open')"
    )
    conn.execute("INSERT INTO search_fts VALUES (1, 'Title', 'Body', 'assertion', 'asrt-old')")
    conn.commit()

    # Must not raise sqlite3.IntegrityError despite FOREIGN KEY(assertion_id) REFERENCES assertion(assertion_id)
    prior_ev, sup_ids = _prepare_fingerprint_replacement(conn, "asrt-old")
    conn.execute("INSERT INTO assertion (assertion_id) VALUES ('asrt-new')")
    _restore_fingerprint_replacement(
        conn,
        new_assertion_id="asrt-new",
        prior_ev=prior_ev,
        superseded_ids=sup_ids,
        evidence_ids=[],
        existing_evidence=set(),
        now="2026-10-28T00:00:00Z",
        old_assertion_id="asrt-old",
    )
    conn.commit()

    vq_row = conn.execute(
        "SELECT target_id, assertion_id, claim_id, status FROM validation_queue WHERE queue_id='vq-1'"
    ).fetchone()
    assert vq_row == ("asrt-new", "asrt-new", "asrt-new", "open")
    fts_old = conn.execute("SELECT count(*) FROM search_fts WHERE object_id='asrt-old'").fetchone()[0]
    assert fts_old == 0
    conn.close()


def test_retire_omitted_source_assertions_evicts_fts_and_resolves_validation_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_retire.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            primary_source_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            superseded_at TEXT,
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE search_fts (
            rowid INTEGER,
            title TEXT,
            body TEXT,
            object_type TEXT,
            object_id TEXT
        );
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            target_id TEXT,
            status TEXT DEFAULT 'open',
            updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO assertion VALUES ('a-keep', 'src-canon', 'current', NULL, 1, 't0')")
    conn.execute("INSERT INTO assertion VALUES ('a-omit', 'src-canon', 'current', NULL, 1, 't0')")
    conn.execute("INSERT INTO search_document VALUES ('sd-keep', 'a-keep', 'current', 1, 't0')")
    conn.execute("INSERT INTO search_document VALUES ('sd-omit', 'a-omit', 'current', 1, 't0')")
    _sync_search_fts_row(conn, "sd-keep", "Keep", "Keep body", "a-keep", "current")
    _sync_search_fts_row(conn, "sd-omit", "Omit", "Omit body", "a-omit", "current")
    # Calling _sync_search_fts_row twice must not create duplicate rows
    _sync_search_fts_row(conn, "sd-keep", "Keep", "Keep body", "a-keep", "current")
    assert conn.execute("SELECT count(*) FROM search_fts WHERE object_id='a-keep'").fetchone()[0] == 1

    conn.execute("INSERT INTO validation_queue VALUES ('vq-omit', 'a-omit', 'open', 't0')")
    conn.commit()

    _retire_omitted_source_assertions(
        conn,
        reingested_sources={"src-canon"},
        touched_by_source={"src-canon": {"a-keep"}},
        now="2026-10-28T00:00:00Z",
    )
    conn.commit()

    assert conn.execute("SELECT count(*) FROM search_fts WHERE object_id='a-omit'").fetchone()[0] == 0
    vq_status = conn.execute("SELECT status FROM validation_queue WHERE queue_id='vq-omit'").fetchone()[0]
    assert vq_status == "resolved"
    conn.close()


def test_source_consolidation_repoints_colliding_edge_and_claim_maps(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_consol.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT,
            from_node_id TEXT,
            edge_type TEXT,
            to_node_id TEXT,
            UNIQUE(source_id, from_node_id, edge_type, to_node_id)
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT,
            assertion_id TEXT,
            PRIMARY KEY(edge_id, assertion_id)
        );
        CREATE TABLE claim (
            claim_id TEXT PRIMARY KEY,
            source_id TEXT,
            claim_text TEXT,
            UNIQUE(source_id, claim_text)
        );
        CREATE TABLE legacy_claim_assertion_map (
            claim_id TEXT,
            assertion_id TEXT,
            PRIMARY KEY(claim_id, assertion_id)
        );
        CREATE TABLE claim_confidence_hitl_matrix (
            claim_id TEXT PRIMARY KEY,
            score REAL
        );
        """
    )
    conn.execute("INSERT INTO edge VALUES ('e-win', 'src-win', 'n1', 'LEADS', 'n2')")
    conn.execute("INSERT INTO edge VALUES ('e-lose', 'src-lose', 'n1', 'LEADS', 'n2')")
    conn.execute("INSERT INTO legacy_edge_assertion_map VALUES ('e-lose', 'asrt-100')")

    conn.execute("INSERT INTO claim VALUES ('c-win', 'src-win', 'Alice leads N2')")
    conn.execute("INSERT INTO claim VALUES ('c-lose', 'src-lose', 'Alice leads N2')")
    conn.execute("INSERT INTO legacy_claim_assertion_map VALUES ('c-lose', 'asrt-200')")
    conn.execute("INSERT INTO claim_confidence_hitl_matrix VALUES ('c-lose', 0.95)")
    conn.commit()

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    _repoint_edges_for_source(conn, tables, "src-lose", "src-win")
    _repoint_claims_for_source(conn, tables, "src-lose", "src-win")
    conn.commit()

    edge_map = conn.execute("SELECT edge_id, assertion_id FROM legacy_edge_assertion_map").fetchall()
    assert edge_map == [("e-win", "asrt-100")]
    claim_map = conn.execute("SELECT claim_id, assertion_id FROM legacy_claim_assertion_map").fetchall()
    assert claim_map == [("c-win", "asrt-200")]
    matrix_rows = conn.execute("SELECT claim_id, score FROM claim_confidence_hitl_matrix").fetchall()
    assert matrix_rows == [("c-win", 0.95)]
    conn.close()


def test_retire_unreferenced_retired_sources_respects_edge_and_claim_refs_and_cleans_fts(tmp_path: Path) -> None:
    db_path = tmp_path / "graph_ret_src.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            primary_source_id TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT
        );
        CREATE TABLE claim (
            claim_id TEXT PRIMARY KEY,
            source_id TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            source_id TEXT,
            object_id TEXT
        );
        CREATE TABLE search_fts (
            rowid INTEGER,
            object_id TEXT
        );
        """
    )
    conn.execute("INSERT INTO source VALUES ('src-edge-ref', 'Retired - Has Edge')")
    conn.execute("INSERT INTO edge VALUES ('e-1', 'src-edge-ref')")
    conn.execute("INSERT INTO source VALUES ('src-unref', 'Retired - Unused')")
    conn.execute("INSERT INTO search_document VALUES ('sd-unref', 'src-unref', 'obj-unref')")
    sd_rowid = conn.execute("SELECT rowid FROM search_document WHERE search_document_id='sd-unref'").fetchone()[0]
    conn.execute("INSERT INTO search_fts VALUES (?, 'obj-unref')", (sd_rowid,))
    conn.commit()

    samples: list[str] = []
    retired = _retire_unreferenced_retired_sources(conn, samples)
    conn.commit()

    assert retired == 1
    assert samples == ["src-unref"]
    remaining_sources = [r[0] for r in conn.execute("SELECT source_id FROM source").fetchall()]
    assert remaining_sources == ["src-edge-ref"]
    assert conn.execute("SELECT count(*) FROM search_fts WHERE object_id='obj-unref'").fetchone()[0] == 0
    conn.close()
