"""Adversarial and edge-case tests for runtime pointer hermeticity (Wave 2)."""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest

from dual_pipeline.runtime_root import (
    ActiveGraphMissingError,
    resolve_active_pair,
    resolve_main_repository_root,
)


def test_adversarial_pointer_empty_or_corrupt_fails_closed(tmp_path: Path) -> None:
    """Category: Boundary payload & Malformed input.

    Empty or truncated JSON in pointer file must fail closed immediately.
    """
    p_dir = tmp_path / "corrupt_pointer"
    p_dir.mkdir()
    pair_file = p_dir / "active_pair.json"

    # 1. Zero-byte file
    pair_file.write_text("", encoding="utf-8")
    with pytest.raises(ActiveGraphMissingError):
        resolve_active_pair(p_dir, enforce_integrity=False)

    # 2. Malformed JSON
    pair_file.write_text('{"$schema": "runtime-pointer:1.0.0", "target":', encoding="utf-8")
    with pytest.raises(ActiveGraphMissingError):
        resolve_active_pair(p_dir, enforce_integrity=False)


def test_adversarial_multi_hop_pointer_rejected(tmp_path: Path) -> None:
    """Category: Invariant bypass attempt.

    Chains of pointers (A -> B -> C) are strictly prohibited to prevent infinite loops.
    """
    target_dir = tmp_path / "target_dir"
    target_dir.mkdir()
    (target_dir / "active_pair.json").write_text(
        json.dumps({"generation_id": "gen-final", "graph_path": "dummy.sqlite"}),
        encoding="utf-8",
    )

    hop1_dir = tmp_path / "hop1"
    hop1_dir.mkdir()
    (hop1_dir / "active_pair.json").write_text(
        json.dumps({"$schema": "runtime-pointer:1.0.0", "target": str(target_dir / "active_pair.json")}),
        encoding="utf-8",
    )

    hop2_dir = tmp_path / "hop2"
    hop2_dir.mkdir()
    (hop2_dir / "active_pair.json").write_text(
        json.dumps({"$schema": "runtime-pointer:1.0.0", "target": str(hop1_dir / "active_pair.json")}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Multi-hop pointer chains are forbidden"):
        resolve_active_pair(hop2_dir, enforce_integrity=False)


def test_adversarial_nonexistent_pointer_target_fails_closed(tmp_path: Path) -> None:
    """Category: Rollback & recovery / Fail-closed.

    A pointer pointing to a non-existent file must fail closed with ActiveGraphMissingError.
    """
    p_dir = tmp_path / "broken_pointer"
    p_dir.mkdir()
    (p_dir / "active_pair.json").write_text(
        json.dumps({"$schema": "runtime-pointer:1.0.0", "target": "does_not_exist.json"}),
        encoding="utf-8",
    )

    with pytest.raises(ActiveGraphMissingError):
        resolve_active_pair(p_dir, enforce_integrity=False)


def test_adversarial_production_local_write_intercepted() -> None:
    """Category: Invariant bypass attempt.

    Attempting to write into repository's production .local path during test fails closed.
    """
    main_repo = resolve_main_repository_root()
    forbidden_target = main_repo / ".local" / "dual_pipeline" / "forbidden_test_mutation.txt"

    with pytest.raises(RuntimeError, match="STATE_BLEED_VIOLATION"):
        forbidden_target.write_text("rogue write", encoding="utf-8")


def test_adversarial_concurrent_pointer_resolution_race(tmp_path: Path) -> None:
    """Category: Concurrency & race.

    Concurrent reads across 16 threads must not corrupt or race pointer resolution.
    """
    main_repo = resolve_main_repository_root()
    runtime_dir = main_repo / ".local" / "dual_pipeline" / "runtime"

    def _resolve_task() -> str:
        data, path = resolve_active_pair(runtime_dir, enforce_integrity=True)
        return data["generation_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_resolve_task) for _ in range(50)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    canonical_pair = main_repo / ".local" / "dual_pipeline" / "active_pair.json"
    expected_gen = json.loads(canonical_pair.read_text(encoding="utf-8"))["generation_id"]
    assert len(results) == 50
    assert all(r == expected_gen for r in results)
    assert expected_gen.startswith("generation-")
