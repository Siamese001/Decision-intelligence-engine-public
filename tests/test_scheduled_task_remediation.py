"""tests.test_scheduled_task_remediation

Wave 3 test suite verifying durable findings ledger, stable finding identities,
reproduction tracking, patch verification states, strict closure predicates,
and handoff packages.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.scheduled_task_core.findings import (
    ClosureProof,
    ClosureType,
    DurableFinding,
    DurableFindingsLedger,
    FalseClosureError,
    FindingState,
    InvalidFindingTransitionError,
    PatchVerificationEvidence,
    ReproductionEvidence,
    UntestedPatchError,
)
from tools.scheduled_task_core.handoff import (
    DurableHandoffPackage,
    create_durable_handoff,
)


def test_finding_deduplication_across_three_runs(tmp_path: Path) -> None:
    """Acceptance criterion: The same finding across three runs produces one tracked issue with updated observations."""
    ledger = DurableFindingsLedger(task_id="storage_repository_hygiene")
    sig = "stale_temp_dir:sandbox_eval_9981"

    # Run 1
    f1 = ledger.record_finding(
        signature=sig,
        run_id="run-20261006-001",
        root_cause_hypothesis="Interrupted eval sandbox was not cleaned during exception handler.",
        owner="storage_lead",
    )
    assert f1.occurrence_count == 1
    assert f1.run_ids_observed == ["run-20261006-001"]
    assert f1.state == FindingState.DETECTED

    # Run 2
    f2 = ledger.record_finding(
        signature=sig,
        run_id="run-20261006-002",
    )
    assert f2.finding_id == f1.finding_id
    assert f2.occurrence_count == 2
    assert f2.run_ids_observed == ["run-20261006-001", "run-20261006-002"]

    # Run 3
    f3 = ledger.record_finding(
        signature=sig,
        run_id="run-20261006-003",
    )
    assert f3.finding_id == f1.finding_id
    assert f3.occurrence_count == 3
    assert f3.run_ids_observed == ["run-20261006-001", "run-20261006-002", "run-20261006-003"]

    metrics = ledger.get_metrics()
    assert metrics["total_tracked_findings"] == 1
    assert metrics["repeated_findings"] == 1
    assert metrics["verified_applied_repairs"] == 0


def test_distinct_signatures_create_distinct_findings() -> None:
    ledger = DurableFindingsLedger(task_id="worktree_coverage_cleanup")
    f1 = ledger.record_finding(signature="untracked_stash:stash@{0}", run_id="run-1")
    f2 = ledger.record_finding(signature="unregistered_worktree:.worktrees/temp_01", run_id="run-1")

    assert f1.finding_id != f2.finding_id
    assert len(ledger.findings) == 2
    assert ledger.get_metrics()["total_tracked_findings"] == 2


def test_cancellation_cannot_satisfy_repair_closure() -> None:
    """Acceptance criterion: Cancellation cannot satisfy repair closure."""
    ledger = DurableFindingsLedger(task_id="ssot_drift_remediation")
    finding = ledger.record_finding(
        signature="missing_policy_threshold:INV-001",
        run_id="run-100",
        root_cause_hypothesis="Policy file deleted",
    )

    # Attempt 1: rationale citing cancellation
    invalid_proof_1 = ClosureProof(
        closure_type="DEPLOYED_VERIFIED",
        operator="op-alice",
        rationale="Cancelled scheduled task run and discarded findings",
        timestamp="2026-10-06T15:00:00Z",
        commit_sha="abcd1234ef",
    )
    with pytest.raises(FalseClosureError, match="Cancellation cannot satisfy repair closure"):
        finding.close(invalid_proof_1)

    # Attempt 2: closure_type claiming cancellation
    invalid_proof_2 = ClosureProof(
        closure_type="CANCELLED",
        operator="op-alice",
        rationale="Directive was cancelled",
        timestamp="2026-10-06T15:00:00Z",
    )
    with pytest.raises(FalseClosureError, match="Cancellation cannot satisfy repair closure"):
        finding.close(invalid_proof_2)

    assert finding.state == FindingState.DETECTED
    assert finding.closure_proof is None


def test_untested_patch_remains_proposed() -> None:
    """Acceptance criterion: An untested patch remains proposed."""
    ledger = DurableFindingsLedger(task_id="repository_antipattern_audit")
    finding = ledger.record_finding(
        signature="cyclomatic_complexity:tools/foo.py:func_bar",
        run_id="run-200",
    )

    finding.propose_patch(
        patch_digest="sha256:11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff",
        hypothesis="Decompose func_bar into three sub-functions",
    )
    assert finding.state == FindingState.PATCH_PROPOSED

    # Incomplete test evidence (passing_after=False)
    untested_evidence = PatchVerificationEvidence(
        failing_before=True,
        passing_after=False,
        test_path="tests/test_foo.py",
        test_command="pytest tests/test_foo.py",
    )
    with pytest.raises(UntestedPatchError, match="requires failing_before=True and passing_after=True"):
        finding.verify_patch(untested_evidence)

    # Finding must remain in PATCH_PROPOSED
    assert finding.state == FindingState.PATCH_PROPOSED


def test_verified_local_patch_cannot_be_reported_as_deployed() -> None:
    """Acceptance criterion: A verified local patch cannot be reported as deployed."""
    ledger = DurableFindingsLedger(task_id="knowledge_graph_hygiene")
    finding = ledger.record_finding(
        signature="orphaned_candidate_node:node-492",
        run_id="run-300",
    )

    finding.propose_patch("sha256:abc123def456")
    finding.verify_patch(
        PatchVerificationEvidence(
            failing_before=True,
            passing_after=True,
            test_path="tests/test_kg_cleanup.py",
            test_command="pytest tests/test_kg_cleanup.py -k test_orphaned_node",
        )
    )
    assert finding.state == FindingState.PATCH_VERIFIED

    # Local verified patch cannot jump directly to DEPLOYED_VERIFIED without deployment commit_sha proof
    invalid_closure = ClosureProof(
        closure_type="DEPLOYED_VERIFIED",
        operator="op-bob",
        rationale="Patch passed on my laptop",
        timestamp="2026-10-06T15:00:00Z",
        commit_sha=None,  # Missing deployment commit SHA
    )
    with pytest.raises(FalseClosureError, match="requires commit_sha deployment proof"):
        finding.close(invalid_closure)

    assert finding.state == FindingState.PATCH_VERIFIED

    # Transitions to AWAITING_AUTHORIZATION
    finding.request_authorization()
    assert finding.state == FindingState.AWAITING_AUTHORIZATION

    # Legitimate deployment proof with commit_sha
    valid_closure = ClosureProof(
        closure_type="DEPLOYED_VERIFIED",
        operator="op-bob",
        rationale="Merged and verified via CI commit",
        timestamp="2026-10-06T15:00:00Z",
        commit_sha="c0ffee1234567890abcdef",
    )
    finding.close(valid_closure)
    assert finding.state == FindingState.DEPLOYED_VERIFIED


def test_reproduction_failure_and_failed_tests_remain_visible() -> None:
    """Acceptance criterion: Reproduction failure, failed tests, and absent approval remain visible."""
    ledger = DurableFindingsLedger(task_id="weekly_market_intelligence")
    finding = ledger.record_finding(
        signature="source_feed_timeout:sec_edgar_rss",
        run_id="run-400",
    )

    failed_repro = ReproductionEvidence(
        reproduced=False,
        fixture_path="fixtures/test_sec_feed.json",
        test_command="python -m tools.fetch_feed --fixture fixtures/test_sec_feed.json",
        failing_exit_code=0,
        error_signature="Connection reset was transient and did not reproduce in sandbox",
    )
    finding.mark_reproduced(failed_repro)

    # State must NOT advance to REPRODUCED
    assert finding.state == FindingState.DETECTED
    assert finding.reproduction_evidence.reproduced is False
    assert "transient" in (finding.reproduction_evidence.error_signature or "")


def test_dispositions_require_operator_and_rationale() -> None:
    """Verifies that false positive and accepted risk dispositions require explicit rationale."""
    ledger = DurableFindingsLedger(task_id="pr_queue_observer")
    finding = ledger.record_finding(
        signature="long_running_pr:pr-881",
        run_id="run-500",
    )

    with pytest.raises(FalseClosureError, match="requires operator identity and non-empty rationale"):
        finding.close(ClosureProof(
            closure_type="DISPOSITIONED_ACCEPTED_RISK",
            operator="",
            rationale="",
            timestamp="2026-10-06T15:00:00Z",
        ))

    # Valid accepted risk disposition
    finding.close(ClosureProof(
        closure_type="DISPOSITIONED_ACCEPTED_RISK",
        operator="op-carol",
        rationale="PR 881 is an intentional long-running release evaluation branch.",
        timestamp="2026-10-06T15:00:00Z",
    ))
    assert finding.state == FindingState.DISPOSITIONED_ACCEPTED_RISK
    assert ledger.get_metrics()["dispositioned_findings"] == 1


def test_clean_run_produces_zero_repairs_and_zero_mutations() -> None:
    """Acceptance criterion: No quota requiring graph changes or repairs on clean runs."""
    ledger = DurableFindingsLedger(task_id="knowledge_graph_hygiene")
    metrics = ledger.get_metrics()

    assert metrics["total_tracked_findings"] == 0
    assert metrics["new_findings"] == 0
    assert metrics["repeated_findings"] == 0
    assert metrics["verified_applied_repairs"] == 0
    assert metrics["resolved_findings"] == 0


def test_findings_ledger_schema_conformance(tmp_path: Path) -> None:
    """Verifies complete serialization, schema validation, and persistence."""
    ledger = DurableFindingsLedger(task_id="storage_repository_hygiene")
    f1 = ledger.record_finding(
        signature="orphaned_cache_key:cache_99",
        run_id="run-600",
        root_cause_hypothesis="Cache entry lacked TTL index",
        owner="cache_eng",
        reproduction_evidence=ReproductionEvidence(
            reproduced=True,
            fixture_path="fixtures/cache_dump.json",
            test_command="pytest tests/test_cache.py",
            failing_exit_code=1,
            error_signature="KeyError: cache_99",
        ),
    )
    f1.propose_patch("sha256:778899aabbcc")
    f1.verify_patch(PatchVerificationEvidence(
        failing_before=True,
        passing_after=True,
        test_path="tests/test_cache.py",
        test_command="pytest tests/test_cache.py",
    ))

    ledger_file = tmp_path / "findings_ledger.json"
    ledger.save(ledger_file)

    loaded = DurableFindingsLedger.load(ledger_file)
    assert len(loaded.findings) == 1
    loaded_f1 = loaded.findings[f1.finding_id]
    assert loaded_f1.state == FindingState.PATCH_VERIFIED
    assert loaded_f1.reproduction_evidence.reproduced is True
    assert loaded.get_metrics()["verified_patches"] == 1


def test_durable_handoff_package_creation(tmp_path: Path) -> None:
    """Acceptance criterion: Unauthorized work stops with a durable handoff, not an unattended question loop."""
    authority = AuthorityContext(
        task_id="storage_repository_hygiene",
        run_id="run-700",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        is_scheduled=True,
    )

    ledger = DurableFindingsLedger(task_id="storage_repository_hygiene")
    f1 = ledger.record_finding(
        signature="stale_lock:db_write.lock",
        run_id="run-700",
        reproduction_evidence=ReproductionEvidence(
            reproduced=True,
            fixture_path="fixtures/lock_test.json",
            test_command="python tools/diagnose_lock.py",
            failing_exit_code=2,
        ),
    )

    output_handoff = tmp_path / "handoff_package.json"
    handoff = create_durable_handoff(
        task_id="storage_repository_hygiene",
        run_id="run-700",
        findings=[f1],
        authority=authority,
        output_path=output_handoff,
    )

    assert handoff.handoff_id == "handoff-storage_repository_hygiene-run-700"
    assert handoff.finding_ids == [f1.finding_id]
    assert "Unattended scheduled run lacks authorized repair credentials" in handoff.unattended_stop_reason
    assert "python tools/diagnose_lock.py" in handoff.reproduction_commands
    assert output_handoff.is_file()

    loaded_handoff = DurableHandoffPackage.load(output_handoff)
    assert loaded_handoff.handoff_id == handoff.handoff_id
    assert loaded_handoff.status == "AWAITING_OPERATOR_REVIEW"


def test_absence_resolution() -> None:
    """Verifies that closure via RESOLVED_ON_ABSENCE transitions finding to RESOLVED."""
    ledger = DurableFindingsLedger(task_id="storage_repository_hygiene")
    finding = ledger.record_finding(
        signature="transient_network_glitch:node-1",
        run_id="run-800",
    )
    proof = ClosureProof(
        closure_type=ClosureType.RESOLVED_ON_ABSENCE.value,
        operator="op-dave",
        rationale="Defect was absent across 5 consecutive scans.",
        timestamp="2026-10-06T15:00:00Z",
    )
    finding.close(proof)
    assert finding.state == FindingState.RESOLVED
    assert ledger.get_metrics()["resolved_findings"] == 1
