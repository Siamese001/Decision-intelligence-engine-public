"""Adversarial and Edge-Case test suite for active-pair resolution.

Covers 4 mandatory governance categories:
1. Boundary payload: 0-byte active_pair.json, empty runtime directory.
2. Malformed input: Truncated JSON, missing graph_path, missing graph_sha256.
3. Invariant bypass attempt: Two pointers (multi-hop chaining), dangling pointer.
4. Exact production regression: Missing graph file, SHA-256 digest mismatch,
   ensuring fail-closed behavior with ACTIVE_GRAPH_MISSING.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.cli.operational import _resolve_graph_path
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot
from dual_pipeline.runtime_root import (
    ActiveGraphIntegrityMismatchError,
    ActiveGraphMissingError,
    _compute_sha256,
    resolve_active_graph_path,
    resolve_active_pair,
)


# ==============================================================================
# 1. Boundary Payloads
# ==============================================================================


def test_boundary_missing_active_pair_file() -> None:
    """Empty runtime directory with no active_pair.json fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert isinstance(exc_info.value, ActiveGraphMissingError)


def test_boundary_zero_byte_active_pair_file() -> None:
    """0-byte active_pair.json fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        (tmp_root / "active_pair.json").write_text("", encoding="utf-8")
        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)


# ==============================================================================
# 2. Malformed Inputs
# ==============================================================================


def test_malformed_truncated_json() -> None:
    """Truncated JSON in active_pair.json fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        (tmp_root / "active_pair.json").write_text('{"schema_version": "1.0.0", "generation_id":', encoding="utf-8")
        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)


def test_malformed_missing_graph_path() -> None:
    """active_pair.json missing required graph_path fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        payload = {
            "schema_version": "1.0.0",
            "generation_id": "gen-malformed-01",
            "graph_sha256": "0" * 64,
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert "graph_path" in str(exc_info.value)


def test_malformed_missing_graph_sha256_when_integrity_enforced() -> None:
    """active_pair.json missing graph_sha256 fails closed under integrity check."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        graph_file = tmp_root / "graph.sqlite"
        graph_file.write_text("DUMMY_SQLITE_CONTENT", encoding="utf-8")
        payload = {
            "schema_version": "1.0.0",
            "generation_id": "gen-no-sha-01",
            "graph_path": "graph.sqlite",
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root, enforce_integrity=True)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert "graph_sha256" in str(exc_info.value)


# ==============================================================================
# 3. Invariant Bypass Attempts (Two Pointers / Dangling Pointers)
# ==============================================================================


def test_invariant_bypass_multi_hop_pointer_chain_rejected() -> None:
    """Two-pointer chain (pointer pointing to another pointer) fails closed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        rt1 = tmp_root / "runtime_hop1"
        rt2 = tmp_root / "runtime_hop2"
        rt3 = tmp_root / "runtime_target"
        rt1.mkdir()
        rt2.mkdir()
        rt3.mkdir()

        # Valid target
        graph_file = rt3 / "graph.sqlite"
        graph_file.write_text("REAL_GRAPH_DATA", encoding="utf-8")
        sha = _compute_sha256(graph_file)
        (rt3 / "active_pair.json").write_text(
            json.dumps({"graph_path": "graph.sqlite", "graph_sha256": sha, "generation_id": "gen-t"}),
            encoding="utf-8",
        )

        # Hop 2 points to target
        (rt2 / "active_pair.json").write_text(
            json.dumps({"$schema": "runtime-pointer:1.0.0", "target": str(rt3 / "active_pair.json")}),
            encoding="utf-8",
        )

        # Hop 1 points to Hop 2 (FORBIDDEN MULTI-HOP)
        (rt1 / "active_pair.json").write_text(
            json.dumps({"$schema": "runtime-pointer:1.0.0", "target": str(rt2 / "active_pair.json")}),
            encoding="utf-8",
        )

        with pytest.raises(ValueError) as exc_info:
            resolve_active_pair(rt1)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert "Multi-hop" in str(exc_info.value)


def test_invariant_bypass_dangling_pointer() -> None:
    """Pointer pointing to a non-existent file fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        pointer_data = {
            "$schema": "runtime-pointer:1.0.0",
            "target": str(tmp_root / "does_not_exist" / "active_pair.json"),
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(pointer_data), encoding="utf-8")
        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)


# ==============================================================================
# 4. Exact Production Regressions (Missing Graph / Digest Mismatch)
# ==============================================================================


def test_regression_missing_graph_file_on_disk() -> None:
    """Declared graph file absent on disk fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        pair = {
            "schema_version": "1.0.0",
            "generation_id": "gen-ghost-01",
            "graph_path": "generations/gen-ghost-01/graph.sqlite",
            "graph_sha256": "e" * 64,
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(pair), encoding="utf-8")

        with pytest.raises(ActiveGraphMissingError) as exc_info:
            resolve_active_pair(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert isinstance(exc_info.value, FileNotFoundError)


def test_regression_graph_digest_mismatch() -> None:
    """Corrupted or modified graph file fails closed with ACTIVE_GRAPH_MISSING."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        graph_file = tmp_root / "graph.sqlite"
        graph_file.write_text("CORRUPTED_OR_TAMPERED_DATA", encoding="utf-8")

        pair = {
            "schema_version": "1.0.0",
            "generation_id": "gen-tampered-01",
            "graph_path": "graph.sqlite",
            "graph_sha256": "f" * 64,  # Intentionally mismatched recorded SHA
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(pair), encoding="utf-8")

        with pytest.raises(ActiveGraphIntegrityMismatchError) as exc_info:
            resolve_active_pair(tmp_root, enforce_integrity=True)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
        assert "integrity mismatch" in str(exc_info.value).lower()
        assert isinstance(exc_info.value, ValueError)


def test_regression_cli_resolve_graph_path_no_silent_seed_fallback() -> None:
    """_resolve_graph_path with allow_seed=False fails closed on missing graph."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        # Empty runtime root
        with pytest.raises(FileNotFoundError) as exc_info:
            _resolve_graph_path(None, runtime_root=tmp_root, allow_seed=False)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)


def test_regression_readonly_snapshot_fails_on_missing_graph() -> None:
    """ReadOnlyGraphSnapshot fails closed with ACTIVE_GRAPH_MISSING if graph is missing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        pair = {
            "schema_version": "1.0.0",
            "generation_id": "gen-snap-missing",
            "graph_path": "missing_graph.sqlite",
            "graph_sha256": "a" * 64,
        }
        (tmp_root / "active_pair.json").write_text(json.dumps(pair), encoding="utf-8")

        with pytest.raises(FileNotFoundError) as exc_info:
            ReadOnlyGraphSnapshot(tmp_root)
        assert "ACTIVE_GRAPH_MISSING" in str(exc_info.value)
