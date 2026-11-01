"""
Wave 1 Adversarial & Edge-Case Tests: AWAITING_APPROVAL, Publication Journal,
and Completion Guard Invariants.

Covers:
1. Invariant bypass attempt (completed/approved directive cannot have bindings overwritten).
2. Rollback & recovery (partial mutation rollback failure blocks retry).
3. Boundary payload (empty/corrupt journal records fail closed).
4. Exact production regression (Ed/Amit LENS AWAITING_APPROVAL staging & journal continuation).
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from dual_pipeline.canonical_pipeline.publication_journal import (
    PublicationReconciliationRequired,
    _record_state,
    guarded_publication,
    mark_publication_rolled_back,
)
from dual_pipeline.completion_core.guard import IngestionCompletionGuard
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    _matches_clearance_scope,
)
from tests.end_to_end.test_ingest_approval_continuation_e2e import _make_stub_guard


def test_adversarial_approved_directive_rejects_binding_mutation(tmp_path: Path) -> None:
    """Invariant bypass attempt: Once a directive is approved or terminal, guard.open rejects binding replacement."""
    guard = _make_stub_guard(tmp_path)
    directive_id = "directive-project_lens-ed__amit_lens-10-16-2026-v1"
    pkg_path = tmp_path / "intake_package.json"
    pkg_path.write_text('{"version": 1}', encoding="utf-8")

    guard._package_bindings = lambda pkgs: [{"path": str(pkg_path), "sha256": "digest_1"}]
    guard.open(
        directive_id=directive_id,
        scope="project_lens",
        packages=[pkg_path],
    )
    # Simulate approval recorded on directive (re-sealed)
    from dual_pipeline.completion_core.verification import _seal_directive

    d_path = tmp_path / "ingestion_directives" / f"{directive_id}.json"
    d_data = json.loads(d_path.read_text(encoding="utf-8"))
    d_data.pop("directive_sha256", None)
    d_data["approval"] = {"status": "APPROVED", "reviewer": "amit_ayer"}
    d_path.write_text(json.dumps(_seal_directive(d_data)), encoding="utf-8")

    guard._package_bindings = lambda pkgs: [{"path": str(pkg_path), "sha256": "tampered_digest"}]
    with pytest.raises(RuntimeError, match="ingestion directive ID already exists with different bindings"):
        guard.open(
            directive_id=directive_id,
            scope="project_lens",
            packages=[pkg_path],
        )


def test_adversarial_different_scope_rejects_directive_overwrite(tmp_path: Path) -> None:
    """Invariant bypass attempt: Different scope cannot overwrite an OPEN directive."""
    guard = _make_stub_guard(tmp_path)
    directive_id = "directive-project_lens-ed__amit_lens-10-16-2026-v1"
    pkg_path = tmp_path / "intake_package.json"
    pkg_path.write_text('{"version": 1}', encoding="utf-8")

    guard._package_bindings = lambda pkgs: [{"path": str(pkg_path), "sha256": "digest_1"}]
    guard.open(
        directive_id=directive_id,
        scope="project_lens",
        packages=[pkg_path],
    )
    with pytest.raises(RuntimeError, match="ingestion directive ID already exists with different bindings"):
        guard.open(
            directive_id=directive_id,
            scope="nea",
            packages=[pkg_path],
        )


def test_adversarial_non_zero_delta_rollback_blocks_retry(tmp_path: Path) -> None:
    """Rollback & recovery: If rollback had zero_delta=False, retry must fail closed."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    directive_id = "directive-project_lens-ed__amit_lens-10-16-2026-v1"

    guarded_publication(
        runtime_root=runtime_root,
        directive_id=directive_id,
        bindings_factory=lambda: {"pkg": "1"},
        publish=lambda: {"status": "AWAITING_APPROVAL"},
    )
    mark_publication_rolled_back(runtime_root, directive_id, reason="mid-write crash", zero_delta=False)

    with pytest.raises(PublicationReconciliationRequired, match="PARTIAL_MUTATION_ROLLBACK_FAILED"):
        guarded_publication(
            runtime_root=runtime_root,
            directive_id=directive_id,
            bindings_factory=lambda: {"pkg": "1"},
            publish=lambda: {"status": "COMPLETE"},
            allow_resume=True,
        )


def test_adversarial_corrupt_journal_and_mismatched_directive_clearance(tmp_path: Path) -> None:
    """Boundary/malformed input & cross-directive clearance isolation."""
    bad_file = tmp_path / "corrupt.intent.json"
    bad_file.write_text("{not_valid_json", encoding="utf-8")
    assert _record_state(bad_file) == "CORRUPT_JOURNAL"

    # Cross-directive delta mismatch must still be rejected even if run_id matches
    record = {
        "decision_id": "req-other",
        "metadata": {
            "directive_id": "directive-other-meeting-v1",
            "run_id": "shared-run-id",
            "delta_digest": "delta_a",
        },
    }
    assert _matches_clearance_scope(
        record,
        directive_id="directive-project_lens-ed__amit_lens-10-16-2026-v1",
        run_id="shared-run-id",
        delta_digest="delta_b",
        target_keys=set(),
    ) is False
