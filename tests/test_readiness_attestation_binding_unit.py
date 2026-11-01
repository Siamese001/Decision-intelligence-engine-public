"""tests/test_readiness_attestation_binding_unit.py

Unit tests for readiness attestation binding, exact base-SHA validation,
and out-of-tree DSSE predicate fields (Wave 1).
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from tools.readiness_planner import validate_readiness_cache_binding
from tools.readiness_core import _save_readiness_receipt
from tools.attestation_core import (
    READINESS_PREDICATE_TYPE,
    compute_tree_digest,
    load_attestation_by_tree,
    save_readiness_attestation,
)


def test_validate_readiness_cache_binding_matching_shas():
    cached_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "commit_head_123",
        "base_commit_sha": "commit_base_456",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="commit_head_123",
        current_base_sha="commit_base_456",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is True
    assert reason == "VALID_REUSE"


def test_validate_readiness_cache_binding_base_mismatch():
    cached_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "commit_head_123",
        "base_commit_sha": "commit_base_old",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="commit_head_123",
        current_base_sha="commit_base_new",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Base commit SHA mismatch" in reason


def test_validate_readiness_cache_binding_head_mismatch():
    cached_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "commit_head_old",
        "base_commit_sha": "commit_base_456",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="commit_head_new",
        current_base_sha="commit_base_456",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Head commit SHA mismatch" in reason


def test_validate_readiness_cache_binding_manifest_version_mismatch():
    cached_entry = {
        "terminal_outcome": "PASS",
        "head_commit_sha": "commit_head_123",
        "base_commit_sha": "commit_base_456",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="commit_head_123",
        current_base_sha="commit_base_456",
        current_manifest_version="2.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Policy manifest version mismatch" in reason


def test_validate_readiness_cache_binding_failed_outcome():
    cached_entry = {
        "terminal_outcome": "FAIL",
        "head_commit_sha": "commit_head_123",
        "base_commit_sha": "commit_base_456",
        "manifest_version": "1.0.0",
        "is_live": True,
    }
    valid, reason = validate_readiness_cache_binding(
        cached_entry,
        current_head_sha="commit_head_123",
        current_base_sha="commit_base_456",
        current_manifest_version="1.0.0",
        is_live_mode=True,
    )
    assert valid is False
    assert "Failure is never reusable" in reason


def test_save_readiness_receipt_contains_base_and_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        receipt_file = tmp_root / "reports" / "readiness" / "test_receipt.json"
        stage_results = [{"stage_index": 1, "stage_name": "Test Stage", "status": "PASS", "duration_seconds": 0.5}]
        proportional_plan = {"risk_lane": 1, "manifest_version": "1.5.0", "proportional_applied": True}

        _save_readiness_receipt(
            branch="feat/test-branch",
            head_sha="head_abc_123",
            terminal_outcome="PASS",
            total_stages=1,
            stage_results=stage_results,
            branch_receipt_path=receipt_file,
            update_global=False,
            repo_root=tmp_root,
            proportional_plan=proportional_plan,
        )

        assert receipt_file.is_file()
        data = json.loads(receipt_file.read_text(encoding="utf-8"))
        assert data["head_commit_sha"] == "head_abc_123"
        assert "base_commit_sha" in data
        assert data["manifest_version"] == "1.5.0"
        assert data["stages"] == stage_results


def test_save_readiness_attestation_dsse_payload():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        stage_results = [{"stage_index": 1, "stage_name": "Test Stage", "status": "PASS", "duration_seconds": 0.5}]
        predicate = {
            "branch": "feat/dsse-branch",
            "head_commit_sha": "sha_head_999",
            "base_commit_sha": "sha_base_000",
            "manifest_version": "1.0.0",
            "terminal_outcome": "PASS",
            "total_stages": 1,
            "stages": stage_results,
            "stage_results": stage_results,
        }

        att_path = save_readiness_attestation(
            repo_root=tmp_root,
            predicate=predicate,
            tree_digest="tree_digest_1234567890abcdef",
            head_sha="sha_head_999",
        )

        assert att_path.is_file()
        stmt = load_attestation_by_tree(
            tmp_root,
            tree_digest="tree_digest_1234567890abcdef",
            head_sha="sha_head_999",
            predicate_type=READINESS_PREDICATE_TYPE,
        )
        assert stmt is not None
        pred = stmt["predicate"]
        assert pred["base_commit_sha"] == "sha_base_000"
        assert pred["manifest_version"] == "1.0.0"
        assert pred["stages"] == stage_results
