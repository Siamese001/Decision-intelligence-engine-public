"""Unit tests for Wave 2 evidence bundle sealing, cycle prevention, honest coverage, and verification."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
from pathlib import Path

import pytest

from tools.scheduled_task_core.coverage import (
    EvaluationSetRecord,
    GateEvaluationRecord,
    GateState,
    TimeWindow,
    TypedCoverageReconciliation,
)
from tools.scheduled_task_core.evidence import (
    DuplicateMemberIdError,
    ImmutableBundleOverwriteError,
    ManifestSelfReferenceError,
    PathTraversalSecurityError,
    TamperedArtifactError,
    build_and_seal_evidence_bundle,
    compute_canonical_digest,
    compute_file_sha256,
    verify_sealed_evidence_bundle,
)


def _setup_mock_artifacts(bundle_dir: Path) -> list[tuple[Path, str]]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    report_file = bundle_dir / "report.md"
    report_file.write_text("# Executive Report\n\nAll systems operational.\n", encoding="utf-8")

    result_file = bundle_dir / "result.json"
    result_file.write_text(json.dumps({"status": "SUCCESS", "items": 42}, indent=2), encoding="utf-8")

    log_file = bundle_dir / "execution.log"
    log_file.write_text("2026-10-06T18:00:00Z [INFO] Execution completed cleanly.\n", encoding="utf-8")

    return [
        (report_file, "report_markdown"),
        (result_file, "structured_result"),
        (log_file, "execution_log"),
    ]


def test_final_payload_first_and_manifest_excludes_itself():
    """Acceptance criterion:

    Write final payload artifacts first; hash exact bytes into a manifest
    that excludes itself from the member list.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_att1"
        bundle_root.mkdir(parents=True)
        artifacts = _setup_mock_artifacts(bundle_root)

        reconciliation = TypedCoverageReconciliation(
            task_id="knowledge_graph_hygiene",
            run_id="run-001",
            expected_evaluation_ids=["eval-1"],
        )
        rec = EvaluationSetRecord(
            evaluation_id="eval-1",
            operation_id="op-1",
            runtime_root="storage/runtime",
            gates={"g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS)},
        )
        reconciliation.record_evaluation(rec)

        bundle = build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="knowledge_graph_hygiene",
            run_id="run-001",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        manifest_path = bundle_root / "manifest.json"
        assert manifest_path.is_file()

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Verify manifest.json is NOT in members
        member_paths = [m["relative_path"] for m in manifest_data["members"]]
        assert "manifest.json" not in member_paths
        assert "./manifest.json" not in member_paths

        # Verify canonical digest excludes manifest_digest itself
        assert "manifest_digest" in manifest_data
        calculated = compute_canonical_digest(manifest_data)
        assert manifest_data["manifest_digest"] == calculated


def test_manifest_self_reference_rejection():
    """Acceptance criterion: Explicitly adding manifest.json to members list raises ManifestSelfReferenceError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_att2"
        bundle_root.mkdir(parents=True)
        artifacts = _setup_mock_artifacts(bundle_root)

        # Illegally include manifest.json in artifact list
        fake_manifest = bundle_root / "manifest.json"
        fake_manifest.write_text("{}", encoding="utf-8")
        artifacts.append((fake_manifest, "structured_result"))

        reconciliation = TypedCoverageReconciliation(task_id="pr_queue_observer", run_id="run-002")

        with pytest.raises(ManifestSelfReferenceError, match="Self-reference violation"):
            build_and_seal_evidence_bundle(
                bundle_root=bundle_root,
                task_id="pr_queue_observer",
                run_id="run-002",
                attempt_id="1",
                coverage_reconciliation=reconciliation,
                artifact_files=artifacts,
                allow_overwrite=True,
            )


def test_independent_verification_passes_clean_bundle():
    """Acceptance criterion: Independent verification passes an unmodified sealed bundle."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_clean"
        artifacts = _setup_mock_artifacts(bundle_root)
        reconciliation = TypedCoverageReconciliation(task_id="weekly_market_intelligence", run_id="run-003")

        build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="weekly_market_intelligence",
            run_id="run-003",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        valid, errors = verify_sealed_evidence_bundle(bundle_root)
        assert valid is True
        assert len(errors) == 0


def test_tampered_markdown_fails_verification():
    """Acceptance criterion: Tampered Markdown fails post-publication verification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_tampered"
        artifacts = _setup_mock_artifacts(bundle_root)
        reconciliation = TypedCoverageReconciliation(task_id="storage_repository_hygiene", run_id="run-004")

        build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="storage_repository_hygiene",
            run_id="run-004",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        # Tamper with markdown after sealing
        report_file = bundle_root / "report.md"
        report_file.write_text("# Malicious Edit\nModified content.\n", encoding="utf-8")

        valid, errors = verify_sealed_evidence_bundle(bundle_root)
        assert valid is False
        assert any("TAMPERED" in err for err in errors)


def test_missing_log_fails_verification():
    """Acceptance criterion: Missing execution log fails post-publication verification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_missing_log"
        artifacts = _setup_mock_artifacts(bundle_root)
        reconciliation = TypedCoverageReconciliation(task_id="worktree_coverage_cleanup", run_id="run-005")

        build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="worktree_coverage_cleanup",
            run_id="run-005",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        # Delete log file
        (bundle_root / "execution.log").unlink()

        valid, errors = verify_sealed_evidence_bundle(bundle_root)
        assert valid is False
        assert any("Missing member artifact" in err for err in errors)


def test_changed_manifest_digest_fails_verification():
    """Acceptance criterion: Modified manifest contents fail digest verification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_bad_manifest"
        artifacts = _setup_mock_artifacts(bundle_root)
        reconciliation = TypedCoverageReconciliation(task_id="ssot_drift_remediation", run_id="run-006")

        build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="ssot_drift_remediation",
            run_id="run-006",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        # Alter manifest metadata without updating canonical digest
        manifest_path = bundle_root / "manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["attempt_id"] = "attempt-99-tampered"
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        valid, errors = verify_sealed_evidence_bundle(bundle_root)
        assert valid is False
        assert any("Manifest digest mismatch" in err for err in errors)


def test_duplicate_member_paths_rejected():
    """Acceptance criterion: Duplicate member paths fail closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_dup"
        artifacts = _setup_mock_artifacts(bundle_root)
        # Add duplicate entry for report.md
        artifacts.append((bundle_root / "report.md", "report_markdown"))
        reconciliation = TypedCoverageReconciliation(task_id="repository_antipattern_audit", run_id="run-007")

        with pytest.raises(DuplicateMemberIdError, match="Duplicate member path detected"):
            build_and_seal_evidence_bundle(
                bundle_root=bundle_root,
                task_id="repository_antipattern_audit",
                run_id="run-007",
                attempt_id="1",
                coverage_reconciliation=reconciliation,
                artifact_files=artifacts,
            )


def test_path_escape_rejected():
    """Acceptance criterion: Path escape attempts outside bundle root are blocked."""
    with tempfile.TemporaryDirectory() as tmpdir:
        outer_dir = Path(tmpdir)
        bundle_root = outer_dir / "bundle_inner"
        bundle_root.mkdir()

        # File located outside bundle root
        escaped_file = outer_dir / "outside_secret.txt"
        escaped_file.write_text("secret", encoding="utf-8")

        reconciliation = TypedCoverageReconciliation(task_id="knowledge_graph_hygiene", run_id="run-008")

        with pytest.raises(ValueError):  # relative_to will fail or path traversal guard
            build_and_seal_evidence_bundle(
                bundle_root=bundle_root,
                task_id="knowledge_graph_hygiene",
                run_id="run-008",
                attempt_id="1",
                coverage_reconciliation=reconciliation,
                artifact_files=[(escaped_file, "input_artifact")],
            )


def test_rerun_attempting_overwrite_fails():
    """Acceptance criterion: Overwriting an already sealed evidence bundle fails closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_root = Path(tmpdir) / "bundle_locked"
        artifacts = _setup_mock_artifacts(bundle_root)
        reconciliation = TypedCoverageReconciliation(task_id="pr_queue_observer", run_id="run-009")

        build_and_seal_evidence_bundle(
            bundle_root=bundle_root,
            task_id="pr_queue_observer",
            run_id="run-009",
            attempt_id="1",
            coverage_reconciliation=reconciliation,
            artifact_files=artifacts,
        )

        # Attempt second write to same bundle without allow_overwrite
        with pytest.raises(ImmutableBundleOverwriteError, match="already sealed"):
            build_and_seal_evidence_bundle(
                bundle_root=bundle_root,
                task_id="pr_queue_observer",
                run_id="run-009",
                attempt_id="1",
                coverage_reconciliation=reconciliation,
                artifact_files=artifacts,
            )


def test_evaluations_expected_vs_inspected_coverage():
    """Acceptance criterion:

    Coverage lists exact expected evaluations and gates. Inspecting 1 of 3 yields partial coverage;
    11 passing gates on 1 run cannot certify the other 2. Missing/unknown is never rendered as 0 or PASS.
    """
    reconciliation = TypedCoverageReconciliation(
        task_id="knowledge_graph_hygiene",
        run_id="run-eval-01",
        expected_evaluation_ids=["eval-A", "eval-B", "eval-C"],
    )

    # Inspect only eval-A with 11 passing gates
    eval_a = EvaluationSetRecord(
        evaluation_id="eval-A",
        operation_id="op-101",
        runtime_root="storage/runtime_1",
        gates={f"gate_{i}": GateEvaluationRecord(gate_id=f"gate_{i}", state=GateState.PASS) for i in range(1, 12)},
    )
    reconciliation.record_evaluation(eval_a)

    d = reconciliation.to_dict()
    assert d["total_expected_evaluations"] == 3
    assert d["total_inspected_evaluations"] == 1
    assert d["coverage_ratio"] == 0.3333
    assert d["audit_status"] == "partial"
    assert d["gates_passed"] == 11

    # Inspecting 1 of 3 CANNOT yield complete status even though 11 gates passed
    assert d["audit_status"] != "complete"


def test_discovery_failure_blocks_full_coverage():
    """Acceptance criterion: Required expected-set discovery failure blocks full-coverage claims."""
    reconciliation = TypedCoverageReconciliation(
        task_id="ssot_drift_remediation",
        run_id="run-disc-01",
        expected_evaluation_ids=["eval-1"],
    )
    eval_1 = EvaluationSetRecord(
        evaluation_id="eval-1",
        operation_id="op-1",
        runtime_root="storage/runtime",
        gates={"g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS)},
    )
    reconciliation.record_evaluation(eval_1)
    # Record collector discovery failure
    reconciliation.record_collector_failure("stage_discovery", "Timeout connecting to secondary index.")

    d = reconciliation.to_dict()
    assert d["audit_status"] == "partial"
    assert len(d["collector_failures"]) == 1


def test_utc_time_window_bounds_start_inclusive_end_exclusive():
    """Acceptance criterion: Enforce both bounds of declared UTC time window (start inclusive, end exclusive)."""
    start = dt.datetime(2026, 10, 6, 0, 0, 0, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 10, 6, 12, 0, 0, tzinfo=dt.timezone.utc)
    window = TimeWindow(start_utc=start, end_utc=end)

    # Start bound is inclusive
    assert window.contains("2026-10-06T00:00:00Z") is True
    # Inside window
    assert window.contains("2026-10-06T06:00:00Z") is True
    # End bound is exclusive
    assert window.contains("2026-10-06T12:00:00Z") is False
    # Outside window
    assert window.contains("2026-10-05T23:59:59Z") is False
    assert window.contains("2026-10-06T12:00:01Z") is False

    reconciliation = TypedCoverageReconciliation(
        task_id="weekly_market_intelligence",
        run_id="run-time-01",
        time_window=window,
        expected_evaluation_ids=["eval-inside", "eval-outside"],
    )

    ev_in = EvaluationSetRecord(
        evaluation_id="eval-inside",
        operation_id="op-1",
        runtime_root="storage/r1",
        timestamp_utc="2026-10-06T06:00:00Z",
        gates={"g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS)},
    )
    ev_out = EvaluationSetRecord(
        evaluation_id="eval-outside",
        operation_id="op-2",
        runtime_root="storage/r1",
        timestamp_utc="2026-10-06T12:00:00Z",  # Exactly at end bound -> excluded
        gates={"g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS)},
    )

    reconciliation.record_evaluation(ev_in)
    reconciliation.record_evaluation(ev_out)

    d = reconciliation.to_dict()
    assert d["total_inspected_evaluations"] == 1
    assert len(d["excluded_items"]) == 1
    assert d["excluded_items"][0]["reason"] == "EXCLUDED_OUTSIDE_TIME_WINDOW"


def test_operation_and_runtime_deduplication():
    """Acceptance criterion: Deduplicate by runtime and operation identity; distinct operations stay distinct."""
    reconciliation = TypedCoverageReconciliation(task_id="knowledge_graph_hygiene", run_id="run-dedup-01")

    # Distinct operations sharing evaluation ID
    rec1 = EvaluationSetRecord(
        evaluation_id="eval-common-name",
        operation_id="directive-001",
        runtime_root="storage/runtime_alpha",
    )
    rec2 = EvaluationSetRecord(
        evaluation_id="eval-common-name",
        operation_id="directive-002",
        runtime_root="storage/runtime_beta",
    )

    reconciliation.record_evaluation(rec1)
    reconciliation.record_evaluation(rec2)

    assert reconciliation.total_inspected_evaluations == 2
    assert rec1.identity_key != rec2.identity_key


def test_unreadable_evidence_stays_in_denominator_preventing_fake_100_percent():
    """Acceptance criterion: Malformed or unreadable evidence stays in expected denominator with error."""
    reconciliation = TypedCoverageReconciliation(
        task_id="storage_repository_hygiene",
        run_id="run-unreadable-01",
        expected_evaluation_ids=["eval-valid-1"],
    )

    valid_rec = EvaluationSetRecord(
        evaluation_id="eval-valid-1",
        operation_id="op-1",
        runtime_root="storage/r1",
        gates={"g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS)},
    )
    reconciliation.record_evaluation(valid_rec)

    # Record unreadable corrupt evaluation file
    reconciliation.record_unreadable_item(
        raw_identifier="corrupt_eval_2.json",
        operation_id="op-corrupt",
        runtime_root="storage/r1",
        error_message="JSONDecodeError: Unterminated string starting at line 14",
    )

    d = reconciliation.to_dict()
    # Total expected is 2, inspected is 1 -> coverage is 50%, NOT 100%!
    assert d["total_expected_evaluations"] == 2
    assert d["total_inspected_evaluations"] == 1
    assert d["unreadable_items_count"] == 1
    assert d["coverage_ratio"] == 0.5
    assert d["audit_status"] == "partial"


def test_shared_json_and_markdown_renderer_agreement():
    """Acceptance criterion: Derived totals and percentages must agree in JSON and Markdown."""
    reconciliation = TypedCoverageReconciliation(
        task_id="weekly_market_intelligence",
        run_id="run-render-01",
        expected_evaluation_ids=["eval-1", "eval-2"],
    )
    e1 = EvaluationSetRecord(
        evaluation_id="eval-1",
        operation_id="op-1",
        runtime_root="storage/r1",
        gates={
            "g1": GateEvaluationRecord(gate_id="g1", state=GateState.PASS),
            "g2": GateEvaluationRecord(gate_id="g2", state=GateState.FAIL),
        },
    )
    reconciliation.record_evaluation(e1)

    json_dict = reconciliation.to_dict()
    md_text = reconciliation.render_markdown()

    # Verify Markdown reflects the exact derived numbers from JSON
    assert f"Expected: {json_dict['total_expected_evaluations']}" in md_text
    assert f"Inspected: {json_dict['total_inspected_evaluations']}" in md_text
    assert f"Coverage Ratio**: `{json_dict['coverage_ratio']} ({json_dict['coverage_percentage']}%)`" in md_text
    assert f"Passed: {json_dict['gates_passed']}" in md_text
    assert f"Failed: {json_dict['gates_failed']}" in md_text
