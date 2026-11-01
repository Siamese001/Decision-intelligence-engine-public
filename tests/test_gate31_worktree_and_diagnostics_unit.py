"""
tests/test_gate31_worktree_and_diagnostics_unit.py

Unit and regression tests verifying Gate 31 fixes for:
1. Worktree runtime root resolution (eliminating CLI vs internal divergence).
2. Hermetic test sandbox isolation (preventing fallback leakage to production active_pair.json).
3. Preservation of exact diagnostic strings (path, hash mismatch) through all copilot stages.
4. Backward-compatible 3-tuple behavior of GraphLoadResult.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.cli.parser import build_parser, DEFAULT_RUNTIME
from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.copilot_cache_graph import (
    GraphLoadResult,
    _resolve_root_dir,
    execute_load_copilot_graph,
    get_last_copilot_graph_diagnostic,
    resolve_graph_paths_and_hash,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _resolve_copilot_graph,
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
    load_copilot_graph,
)
from dual_pipeline.runtime_root import (
    ActiveGraphIntegrityMismatchError,
    resolve_authoritative_runtime_root,
)


def test_cli_parser_defaults_to_authoritative_runtime_root() -> None:
    """Verifies that the CLI parser defaults --runtime-root to authoritative runtime root in worktrees."""
    parser = build_parser()
    args = parser.parse_args(["copilot", "--project", "project_lens", "--request", "open gaps"])
    auth_root = resolve_authoritative_runtime_root(ROOT)
    assert args.runtime_root == auth_root
    assert auth_root == DEFAULT_RUNTIME


def test_resolve_root_dir_worktree_paths() -> None:
    """Verifies that worktree root and worktree default .local/dual_pipeline resolve to authoritative root."""
    auth_root = resolve_authoritative_runtime_root(ROOT)

    # 1. When cand is None
    assert _resolve_root_dir(None) == auth_root

    # 2. When cand is worktree root (which has .git)
    assert _resolve_root_dir(ROOT) == auth_root

    # 3. When cand is worktree .local/dual_pipeline
    worktree_local = ROOT / ".local" / "dual_pipeline"
    assert _resolve_root_dir(worktree_local) == auth_root


def test_resolve_root_dir_isolated_sandbox_fails_closed(tmp_path: Path) -> None:
    """Verifies that an explicit sandbox lacking active_pair.json fails closed and does not leak to prod."""
    sandbox = tmp_path / "isolated_sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)

    # Must return None (fail-closed)
    resolved = _resolve_root_dir(sandbox)
    assert resolved is None

    # Must set diagnostic detail containing sandbox path
    diag = get_last_copilot_graph_diagnostic()
    assert diag is not None
    assert str(sandbox.resolve()) in diag
    assert "active_pair.json not found" in diag

    # resolve_graph_paths_and_hash must fail closed
    r_root, pair_path, g_hash, g_path, pair_data = resolve_graph_paths_and_hash(sandbox, None)
    assert r_root is None
    assert g_path is None


def test_graph_load_result_tuple_compatibility() -> None:
    """Verifies that GraphLoadResult unpacks cleanly as a 3-tuple while exposing diagnostic_detail."""
    res = GraphLoadResult(
        GraphLoadStatus.ABSENT,
        None,
        None,
        diagnostic_detail="active_pair.json missing at /test/path",
    )
    # 3-tuple protocol
    assert isinstance(res, tuple)
    assert len(res) == 3
    assert res[0] == GraphLoadStatus.ABSENT
    assert res[1] is None
    assert res[2] is None

    # Unpacking
    status, in_mem, ret = res
    assert status == GraphLoadStatus.ABSENT
    assert in_mem is None
    assert ret is None

    # Diagnostic attribute
    assert res.diagnostic_detail == "active_pair.json missing at /test/path"


def test_load_copilot_graph_isolated_sandbox_diagnostic(tmp_path: Path) -> None:
    """Verifies load_copilot_graph returns ABSENT and exposes the missing path in diagnostic_detail."""
    sandbox = tmp_path / "sandbox_no_pair"
    sandbox.mkdir(parents=True, exist_ok=True)

    status, in_mem, retriever = load_copilot_graph(runtime_root=sandbox)
    assert status == GraphLoadStatus.ABSENT
    assert in_mem is None
    assert retriever is None

    diag = get_last_copilot_graph_diagnostic()
    assert diag is not None
    assert str(sandbox.resolve()) in diag


def test_load_copilot_graph_sha_mismatch_diagnostic(tmp_path: Path) -> None:
    """Verifies load_copilot_graph returns HASH_MISMATCH and exposes the exact SHA-256 mismatch."""
    mismatch_dir = tmp_path / "mismatch_runtime"
    mismatch_dir.mkdir(parents=True, exist_ok=True)
    db_file = mismatch_dir / "graph.sqlite"
    db_file.write_bytes(b"SQLite format 3\x00dummy data")

    bogus_hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    pair_content = {
        "schema_version": "1.0.0",
        "graph_path": "graph.sqlite",
        "graph_sha256": bogus_hash,
        "generation_id": "gen-mismatch-test",
    }
    (mismatch_dir / "active_pair.json").write_text(json.dumps(pair_content), encoding="utf-8")

    res = load_copilot_graph(runtime_root=mismatch_dir)
    status, in_mem, retriever = res
    assert status == GraphLoadStatus.HASH_MISMATCH
    assert in_mem is None
    assert retriever is None

    assert hasattr(res, "diagnostic_detail")
    diag = res.diagnostic_detail or get_last_copilot_graph_diagnostic()
    assert diag is not None
    assert bogus_hash in diag or "mismatch" in diag.lower()


def test_copilot_engine_preserves_actionable_error_message(tmp_path: Path) -> None:
    """Verifies that copilot_engine surfaces exact diagnostic paths in failure details and user message."""
    sandbox = tmp_path / "missing_runtime"
    sandbox.mkdir(parents=True, exist_ok=True)

    args = argparse.Namespace(
        project="project_lens",
        intent="gaps",
        request="open gaps",
        runtime_root=sandbox,
        json=True,
    )
    res = execute_copilot_request(args)
    assert res.get("status") == "FAIL"
    assert res.get("error") == "NO_GRAPH_GROUNDING_ABSENT"

    details = res.get("details", "")
    assert str(sandbox.resolve()) in details
    assert "active_pair.json not found" in details

    user_msg = res.get("user_message", "")
    assert str(sandbox.resolve()) in user_msg
