"""
Adversarial & Edge-Case tests for Wave 6 (CAT-5 P0 & P1):
Covers >= 3 mandatory categories:
1. Concurrency & race / WAL integrity: prepare_promoted_generation_paths uses SQLite online backup API to clone WAL-mode databases including uncommitted-to-main WAL frames, and sync_sidecar_metadata_graph_hash checkpoints WAL so main sidecar file bytes reflect updated graph_sha256.
2. Invariant bypass attempt: _ensure_source_and_track_reingest records canonical source_id (not raw proposed source_id) in reingested_sources so omitted assertions are properly retired.
3. Production regression: strip_hex_suffixes_on_survivors updates search_document and search_fts in lockstep with assertion.scope_text and object_literal.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.graph_writer_core.helpers import (
    _ensure_source_and_track_reingest,
    _retire_omitted_source_assertions,
)
from dual_pipeline.uwg_core.universal_write_gateway_sidecar import (
    sync_sidecar_metadata_graph_hash,
)
from tools.tier2_dedup_core.hygiene_promotion import (
    prepare_promoted_generation_paths,
    strip_hex_suffixes_on_survivors,
)


def test_ensure_source_and_track_reingest_uses_canonical_source_id(tmp_path: Path) -> None:
    db_path = tmp_path / "reingest.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            primary_source_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            superseded_at TEXT,
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE search_document (
            assertion_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO source_version VALUES ('sv-old', 'src-canonical-001')")
    conn.execute("INSERT INTO assertion VALUES ('a-stale', 'src-canonical-001', 'current', NULL, 1, 't0')")
    conn.execute("INSERT INTO search_document VALUES ('a-stale', 'current', 1, 't0')")
    conn.commit()

    writer = MagicMock()
    writer.resolve_canonical_source_id.return_value = "src-canonical-001"
    op_source = {"source_id": "src-raw-999", "source_version_id": "sv-new", "evidence": []}
    reingested: set[str] = set()
    ensured_sv: set[str] = set()
    existing_ev: set[str] = set()

    _ensure_source_and_track_reingest(
        writer, conn, op_source, "sv-new", "2026-10-28T00:00:00Z", reingested, ensured_sv, existing_ev
    )
    assert reingested == {"src-canonical-001"}
    assert op_source["source_id"] == "src-canonical-001"

    _retire_omitted_source_assertions(
        conn, reingested, {"src-canonical-001": {"a-fresh"}}, "2026-10-28T00:00:00Z"
    )
    conn.commit()
    state = conn.execute("SELECT currentness_state FROM assertion WHERE assertion_id='a-stale'").fetchone()[0]
    assert state == "historical"
    conn.close()


def test_strip_hex_suffixes_syncs_search_document_and_fts(tmp_path: Path) -> None:
    db_path = tmp_path / "hex.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            scope_text TEXT,
            object_literal TEXT,
            currentness_state TEXT DEFAULT 'current',
            updated_at TEXT
        );
        CREATE TABLE search_document (
            assertion_id TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            updated_at TEXT
        );
        CREATE TABLE search_fts (
            rowid INTEGER,
            object_id TEXT,
            title TEXT,
            body TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a-1', 'Alice owns SAP [a1b2c3]', 'SAP [a1b2c3]', 'current', 't0')"
    )
    conn.execute(
        "INSERT INTO search_document VALUES ('a-1', 'a-1', 'Alice owns SAP [a1b2c3]', 'Alice owns SAP [a1b2c3]', 't0')"
    )
    sd_rowid = conn.execute("SELECT rowid FROM search_document WHERE assertion_id='a-1'").fetchone()[0]
    conn.execute(
        "INSERT INTO search_fts VALUES (?, 'a-1', 'Alice owns SAP [a1b2c3]', 'Alice owns SAP [a1b2c3]')",
        (sd_rowid,),
    )
    conn.commit()

    strip_hex_suffixes_on_survivors(conn, "2026-10-28T00:00:00Z")
    conn.commit()

    sd_row = conn.execute("SELECT title, body FROM search_document WHERE assertion_id='a-1'").fetchone()
    fts_row = conn.execute("SELECT title, body FROM search_fts WHERE object_id='a-1'").fetchone()
    assert "[a1b2c3]" not in sd_row[0]
    assert "[a1b2c3]" not in fts_row[0]
    conn.close()


def test_wal_safe_clone_and_sidecar_checkpoint(tmp_path: Path) -> None:
    rt_root = tmp_path / "runtime"
    gen_old = rt_root / "generations" / "gen-old"
    gen_old.mkdir(parents=True)
    g_old = gen_old / "graph.sqlite"
    s_old = gen_old / "sidecar.sqlite"

    conn_g = sqlite3.connect(str(g_old))
    conn_g.execute("PRAGMA journal_mode = WAL")
    conn_g.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY)")
    conn_g.execute("INSERT INTO node VALUES ('n-wal-1')")
    conn_g.commit()

    conn_s = sqlite3.connect(str(s_old))
    conn_s.execute("PRAGMA journal_mode = WAL")
    conn_s.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn_s.execute("INSERT INTO metadata VALUES ('graph_sha256', 'old_hash')")
    conn_s.commit()

    (rt_root / "active_pair.json").write_text(
        json.dumps(
            {
                "generation_id": "gen-old",
                "graph_path": "generations/gen-old/graph.sqlite",
                "sidecar_path": "generations/gen-old/sidecar.sqlite",
            }
        ),
        encoding="utf-8",
    )

    _, _, _, _, new_g, new_s, _ = prepare_promoted_generation_paths(rt_root, "run-wal")
    conn_g.close()
    conn_s.close()

    check_g = sqlite3.connect(str(new_g))
    assert check_g.execute("SELECT node_id FROM node").fetchall() == [("n-wal-1",)]
    check_g.close()

    sync_sidecar_metadata_graph_hash(new_s, "new_checkpointed_sha256_value")
    raw_bytes = new_s.read_bytes()
    assert b"new_checkpointed_sha256_value" in raw_bytes
