"""End-to-end tests for runtime pointer hermeticity and state bleed prevention (Wave 2)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)
from dual_pipeline.runtime_root import (
    resolve_active_graph_path,
    resolve_active_pair,
    resolve_authoritative_runtime_root,
    resolve_main_repository_root,
)


@pytest.mark.e2e
def test_runtime_pointer_resolves_canonical_production_generation() -> None:
    """Verify that .local/dual_pipeline/runtime/active_pair.json pointer cleanly resolves

    to the canonical production generation and active graph file.
    """
    main_repo = resolve_main_repository_root()
    runtime_dir = main_repo / ".local" / "dual_pipeline" / "runtime"
    runtime_pair = runtime_dir / "active_pair.json"

    assert runtime_pair.is_file(), f"Missing runtime active_pair at {runtime_pair}"

    # Read raw content to verify it's a governed pointer
    raw_data = json.loads(runtime_pair.read_text(encoding="utf-8"))
    assert raw_data.get("$schema") == "runtime-pointer:1.0.0"

    # Resolve active pair through the pointer
    data, actual_pair_path = resolve_active_pair(runtime_dir, enforce_integrity=True)

    canonical_pair = main_repo / ".local" / "dual_pipeline" / "active_pair.json"
    assert actual_pair_path == canonical_pair.resolve()
    canonical_data = json.loads(canonical_pair.read_text(encoding="utf-8"))
    assert data["generation_id"] == canonical_data["generation_id"]
    assert data["generation_id"].startswith("generation-")

    # Verify active graph path exists and matches recorded SHA-256
    graph_path = resolve_active_graph_path(runtime_dir, enforce_integrity=True)
    assert graph_path.is_file()
    assert graph_path.stat().st_size > 1024 * 1024  # > 1MB


@pytest.mark.e2e
def test_copilot_retriever_sandbox_isolation_no_state_bleed(tmp_path: Path) -> None:
    """Verify load_copilot_graph with isolated sandbox does not bleed into production root."""
    empty_sandbox = tmp_path / "isolated_sandbox"
    empty_sandbox.mkdir()

    # When explicit runtime_root is provided without active_pair.json, must fail ABSENT
    # without falling back to production .local/dual_pipeline
    status, topology, retriever = load_copilot_graph(runtime_root=empty_sandbox)
    assert status == GraphLoadStatus.ABSENT
    assert topology is None
    assert retriever is None


@pytest.mark.e2e
def test_active_pair_checksum_invariance_under_copilot_execution() -> None:
    """Verify that executing load_copilot_graph against production leaves checksums unchanged."""
    main_repo = resolve_main_repository_root()
    prod_local = main_repo / ".local" / "dual_pipeline"
    ap_path = prod_local / "active_pair.json"
    rt_path = prod_local / "runtime" / "active_pair.json"

    sha_before_ap = hashlib.sha256(ap_path.read_bytes()).hexdigest()
    sha_before_rt = hashlib.sha256(rt_path.read_bytes()).hexdigest()

    # Load copilot graph from authoritative runtime
    status, topology, retriever = load_copilot_graph(
        runtime_root=None,
        attendees=["Amit", "Alex"],
    )

    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR

    # Verify checksums are strictly identical
    sha_after_ap = hashlib.sha256(ap_path.read_bytes()).hexdigest()
    sha_after_rt = hashlib.sha256(rt_path.read_bytes()).hexdigest()

    assert sha_after_ap == sha_before_ap, "Production active_pair.json was mutated!"
    assert sha_after_rt == sha_before_rt, "Production runtime/active_pair.json was mutated!"
