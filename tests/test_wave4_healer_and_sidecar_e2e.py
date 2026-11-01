"""tests/test_wave4_healer_and_sidecar_e2e.py

End-to-End verification of Wave 4:
1. healing_mesh imports cleanly without broken ontology modules.
2. publish_helper._re_seal_active_pair_if_needed synchronizes vector sidecar metadata.
3. tools/lint_no_transcript_overfitting AST scanning detects synthetic/overfitting predicates.
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


def test_healing_mesh_imports_cleanly():
    """Verify healing_mesh imports without error and does not import removed modules."""
    import dual_pipeline.transcript_qa_core.healing_mesh as hm
    assert hasattr(hm, "DeterministicHealer")

    import inspect
    source = inspect.getsource(hm)
    assert "jarvis_kickoff_ontology" not in source
    assert "standup_healer" not in source


def test_re_seal_active_pair_syncs_sidecar_metadata():
    """Verify _re_seal_active_pair_if_needed updates vector sidecar metadata graph_sha256."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        graph_file = root / "canonical_graph.db"
        graph_file.write_bytes(b"sample sqlite canonical graph content for testing")
        graph_sha = _compute_sha256(graph_file)

        sidecar_file = root / "vector_sidecar.sqlite"
        conn = get_db_connection(sidecar_file)
        try:
            conn.execute("CREATE TABLE sidecar_meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO sidecar_meta VALUES ('graph_sha256', 'old_stale_hash')")
            conn.commit()
        finally:
            conn.close()

        active_pair = root / "active_pair.json"
        active_pair_data = {
            "graph_path": "canonical_graph.db",
            "sidecar_path": "vector_sidecar.sqlite",
            "graph_sha256": "old_stale_hash",
            "sidecar_sha256": "old_sidecar_hash",
        }
        active_pair.write_text(json.dumps(active_pair_data, indent=2), encoding="utf-8")

        manifest_file = root / "pair_manifest.json"
        manifest_data = {
            "graph_sha256": "old_stale_hash",
            "sidecar_sha256": "old_sidecar_hash",
        }
        manifest_file.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Run re-seal
        _re_seal_active_pair_if_needed(root, graph_file)

        # Verify active_pair.json updated
        updated_active = json.loads(active_pair.read_text(encoding="utf-8"))
        assert updated_active["graph_sha256"] == graph_sha
        assert updated_active["sidecar_sha256"] != "old_sidecar_hash"

        # Verify sidecar_meta updated on disk
        conn = get_db_connection(sidecar_file, readonly=True)
        try:
            row = conn.execute("SELECT value FROM sidecar_meta WHERE key='graph_sha256'").fetchone()
            assert row is not None
            assert row[0] == graph_sha
        finally:
            conn.close()


def test_lint_no_transcript_overfitting_ast_scanner():
    """Verify tools/lint_no_transcript_overfitting catches _is_synthetic_ and _is_overfitted_."""
    from tools.lint_no_transcript_overfitting import check_code_content

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        clean_file = tmp_path / "clean.py"
        clean_file.write_text("def compute_score(): return 42\n", encoding="utf-8")
        violations = check_code_content(clean_file)
        assert len(violations) == 0

        dirty1_file = tmp_path / "dirty1.py"
        dirty1_file.write_text("def _is_synthetic_fixture(): return True\n", encoding="utf-8")
        violations1 = check_code_content(dirty1_file)
        assert len(violations1) > 0

        dirty2_file = tmp_path / "dirty2.py"
        dirty2_file.write_text("def _is_overfitted_mandy(): return True\n", encoding="utf-8")
        violations2 = check_code_content(dirty2_file)
        assert len(violations2) > 0
