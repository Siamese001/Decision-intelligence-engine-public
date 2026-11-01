"""tests/test_wave4_healer_and_sidecar_adversarial.py

Adversarial and Edge-Case testing for Wave 4:
1. Boundary payload: missing sidecar file, empty graph DB.
2. Malformed input: corrupted sidecar SQLite table, missing required columns.
3. Rollback & recovery: missing manifest file handled gracefully without unhandled crashes.
4. Exact production regression: active_pair.json without sidecar_path key handles default resolution.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from dual_pipeline.canonical_pipeline.publish_helper import (
    _re_seal_active_pair_if_needed,
)
from dual_pipeline.runtime_root import _compute_sha256
from storage.connection import get_db_connection


def test_boundary_missing_sidecar_file():
    """Verify _re_seal_active_pair_if_needed succeeds even if sidecar does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph_file = root / "canonical_graph.db"
        graph_file.write_bytes(b"boundary graph bytes")
        graph_sha = _compute_sha256(graph_file)

        active_pair = root / "active_pair.json"
        active_pair_data = {
            "graph_path": "canonical_graph.db",
            "graph_sha256": "stale_hash",
        }
        active_pair.write_text(json.dumps(active_pair_data), encoding="utf-8")

        # Must not raise
        _re_seal_active_pair_if_needed(root, graph_file)

        updated_active = json.loads(active_pair.read_text(encoding="utf-8"))
        assert updated_active["graph_sha256"] == graph_sha


def test_malformed_sidecar_table_handled_without_crash():
    """Verify corrupt/malformed sidecar table does not crash re-seal process."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph_file = root / "canonical_graph.db"
        graph_file.write_bytes(b"test graph")

        sidecar_file = root / "vector_sidecar.sqlite"
        conn = get_db_connection(sidecar_file)
        try:
            # Table without key/value columns
            conn.execute("CREATE TABLE sidecar_meta (wrong_col TEXT)")
            conn.commit()
        finally:
            conn.close()

        active_pair = root / "active_pair.json"
        active_pair_data = {
            "graph_path": "canonical_graph.db",
            "sidecar_path": "vector_sidecar.sqlite",
            "graph_sha256": "stale",
        }
        active_pair.write_text(json.dumps(active_pair_data), encoding="utf-8")

        # Function catches exception and logs warning without crash
        _re_seal_active_pair_if_needed(root, graph_file)

        updated_active = json.loads(active_pair.read_text(encoding="utf-8"))
        assert updated_active["graph_sha256"] == _compute_sha256(graph_file)


def test_rollback_and_recovery_missing_manifest_graceful():
    """Verify missing pair_manifest.json does not abort active_pair re-sealing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph_file = root / "canonical_graph.db"
        graph_file.write_bytes(b"test graph recovery")

        active_pair = root / "active_pair.json"
        active_pair.write_text(json.dumps({
            "graph_path": "canonical_graph.db",
            "graph_sha256": "stale",
        }), encoding="utf-8")

        # No pair_manifest.json present
        _re_seal_active_pair_if_needed(root, graph_file)

        updated_active = json.loads(active_pair.read_text(encoding="utf-8"))
        assert updated_active["graph_sha256"] == _compute_sha256(graph_file)
        assert not (root / "pair_manifest.json").exists()


def test_regression_active_pair_resolution_default():
    """Verify default resolution of vector_sidecar.sqlite next to graph when omitted in JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph_file = root / "canonical_graph.db"
        graph_file.write_bytes(b"graph content")
        graph_sha = _compute_sha256(graph_file)

        sidecar_file = root / "vector_sidecar.sqlite"
        conn = get_db_connection(sidecar_file)
        try:
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
        finally:
            conn.close()

        active_pair = root / "active_pair.json"
        # Notice sidecar_path is omitted
        active_pair.write_text(json.dumps({
            "graph_path": "canonical_graph.db",
            "graph_sha256": "stale",
        }), encoding="utf-8")

        _re_seal_active_pair_if_needed(root, graph_file)

        conn = get_db_connection(sidecar_file, readonly=True)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key='graph_sha256'").fetchone()
            assert row is not None
            assert row[0] == graph_sha
        finally:
            conn.close()
