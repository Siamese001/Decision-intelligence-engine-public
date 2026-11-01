"""
Wave 1 E2E Test: Non-Exception AWAITING_APPROVAL Lifecycle, Staging Preservation,
and Directive Binding Continuation.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline.publication_journal import (
    get_publication_checkpoint,
    guarded_publication,
)
from dual_pipeline.completion_core.guard import IngestionCompletionGuard
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    _matches_clearance_scope,
)


def _make_stub_guard(tmp_path: Path) -> IngestionCompletionGuard:
    guard = object.__new__(IngestionCompletionGuard)
    guard.runtime_root = tmp_path.resolve()
    guard.directive_dir = guard.runtime_root / "ingestion_directives"
    guard.classification_dir = guard.directive_dir / "classifications"
    guard.lock_path = guard.runtime_root / "ingestion_directives.lock"
    guard.directive_dir.mkdir(parents=True, exist_ok=True)
    guard.classification_dir.mkdir(parents=True, exist_ok=True)

    class _StubManager:
        def active_pair(self):
            return {
                "generation_id": "generation-1",
                "graph_sha256": "a" * 64,
                "sidecar_sha256": "b" * 64,
            }

    guard.manager = _StubManager()
    guard._baseline_binding = lambda active: {"generation_id": active["generation_id"]}
    guard._embedding_target_binding = lambda active: {"backend": "bge-m3"}
    guard._read = lambda d_id: json.loads(guard._path(d_id).read_text(encoding="utf-8"))
    guard.classify = lambda d_id: {
        "directive": guard._read(d_id),
        "classification": "confidential",
        "classification_path": str(guard.classification_dir / f"{d_id}.json"),
    }
    guard.assert_embedding_requirement = lambda d_id, require_bge_m3=False: None
    return guard


def test_awaiting_approval_suspends_cleanly_and_resumes_publication_e2e(tmp_path: Path) -> None:
    """Verify AWAITING_APPROVAL suspends without RuntimeError/reconciliation lock and resumes cleanly."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    directive_id = "directive-project_lens-ed__amit_lens-10-16-2026-v1"

    bindings_v1 = {"directive_id": directive_id, "package_sha256": "aaa111"}
    bindings_v2 = {"directive_id": directive_id, "package_sha256": "bbb222"}

    # Step 1: First run returns AWAITING_APPROVAL
    res1 = guarded_publication(
        runtime_root=runtime_root,
        directive_id=directive_id,
        bindings_factory=lambda: bindings_v1,
        publish=lambda: {
            "status": "AWAITING_APPROVAL",
            "directive_id": directive_id,
            "request_id": "req-ingest-963004c015001333",
        },
        allow_resume=False,
    )
    assert res1["status"] == "AWAITING_APPROVAL"
    chk1 = get_publication_checkpoint(runtime_root, directive_id)
    assert chk1["intent_exists"] is True
    assert chk1["intent_state"] == "AWAITING_APPROVAL"
    assert chk1["result_exists"] is False

    # Step 2: Resumed run after operator approval with refreshed package digest completes cleanly
    res2 = guarded_publication(
        runtime_root=runtime_root,
        directive_id=directive_id,
        bindings_factory=lambda: bindings_v2,
        publish=lambda: {
            "status": "COMPLETE",
            "directive_id": directive_id,
            "generation_id": "generation-535935d4c37acf59965580c27cba",
        },
        allow_resume=True,
    )
    assert res2["status"] == "COMPLETE"
    chk2 = get_publication_checkpoint(runtime_root, directive_id)
    assert chk2["result_exists"] is True
    assert chk2["result_state"] == "RESULT_RECORDED"


def test_completion_guard_and_clearance_scope_allow_repackaged_pending_directive_e2e(tmp_path: Path) -> None:
    """Verify IngestionCompletionGuard.open and _matches_clearance_scope accept refreshed package digest."""
    guard = _make_stub_guard(tmp_path)
    directive_id = "directive-project_lens-ed__amit_lens-10-16-2026-v1"
    pkg_path = tmp_path / "intake_package.json"
    pkg_path.write_text('{"version": 1}', encoding="utf-8")

    guard._package_bindings = lambda pkgs: [{"path": str(pkg_path), "sha256": "digest_attempt_1"}]
    rec1 = guard.open(
        directive_id=directive_id,
        scope="project_lens",
        packages=[pkg_path],
    )
    assert rec1["status"] == "OPEN"
    assert rec1["packages"][0]["sha256"] == "digest_attempt_1"

    # Re-packaging after healing updates intake_package_sha256 while directive is still OPEN & unapproved
    guard._package_bindings = lambda pkgs: [{"path": str(pkg_path), "sha256": "digest_attempt_2"}]
    rec2 = guard.open(
        directive_id=directive_id,
        scope="project_lens",
        packages=[pkg_path],
    )
    assert rec2["status"] == "OPEN"
    assert rec2["packages"][0]["sha256"] == "digest_attempt_2"

    # Clearance scope matches by directive_id even if delta_digest shifted during re-packaging
    record = {
        "decision_id": "req-ingest-963004c015001333",
        "metadata": {
            "directive_id": directive_id,
            "run_id": "run-attempt-1",
            "delta_digest": "old_delta_digest",
        },
    }
    assert _matches_clearance_scope(
        record,
        directive_id=directive_id,
        run_id="run-attempt-2",
        delta_digest="new_delta_digest",
        target_keys=set(),
    ) is True
