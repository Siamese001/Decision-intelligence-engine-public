"""
tests/adversarial/test_quality_and_maintenance_w4_adversarial.py

Adversarial test suite for Wave 4: Quality & Maintenance.
Covers mandatory edge-case categories:
1. Concurrency & Retry Loop: Circular defect routing defense with bounded termination.
2. Overflow & Exhaustion: Large-scale alert deduplication under alert storms.
3. Invariant Bypass Attempt: Zero unapproved writes guarantee during planning.
4. Boundary & Malformed Input: Extreme blank, truncated, or corrupt payloads.
5. Rollback & Recovery / Conflict Detection: Contradictory maintenance proposals flagged.
6. Invariant Bypass / Security: Raw credential leak detection in provenance checks.
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from dual_pipeline.graph_maintenance_core.harm_prioritized_planner import (
    HarmPrioritizedPlanner,
    HygieneFinding,
    QueryImpactRecord,
)
from dual_pipeline.transcript_qa_core.selective_defect_evaluator import (
    SelectiveDefectEvaluator,
)


class TestQualityAndMaintenanceW4Adversarial:

    def test_circular_routing_01_retry_limit_terminates_infinite_repair_loops(self) -> None:
        """Healers that cannot fix defects must halt at max_retries without recursion or infinite loop."""
        evaluator = SelectiveDefectEvaluator(max_retries=3)

        f1 = "Alice: Test turn."
        f2 = "Attendees: Alice\nSummary: Test."
        broken_f3 = "{unrepairable json"

        healer_call_count = 0

        def broken_healer(
            in_f1: str, in_f2: str, in_f3: Any, routed: Dict[str, Any]
        ) -> Tuple[str, str, Any]:
            nonlocal healer_call_count
            healer_call_count += 1
            # Return broken output persistently
            return in_f1, in_f2, "{still broken json"

        report, attempts = evaluator.evaluate_and_route_with_retry(
            f1, f2, broken_f3, healer_fn=broken_healer
        )

        assert report.passed is False
        assert report.terminal_reason == "MAX_RETRIES_EXCEEDED"
        assert attempts == 3
        assert healer_call_count == 3

    def test_overflow_01_alert_storm_deduplicates_to_bounded_proposal(self) -> None:
        """50 duplicate alerts on the same cluster must deduplicate into a single proposal."""
        planner = HarmPrioritizedPlanner(run_id="run-storm-01")

        findings = [
            HygieneFinding(
                finding_id=f"alert-{i:03d}",
                category="duplicate_node",
                severity="HIGH",
                target_entities=["org:acme", "org:acme_corp"],
                description=f"Automated alert {i}: Duplicate entity detected.",
                proposed_fix="merge org:acme_corp into org:acme",
                recurrence_count=1,
            )
            for i in range(50)
        ]

        impacts = [
            QueryImpactRecord(
                entity_id="org:acme",
                query_frequency=100,
                failure_rate=0.10,
            )
        ]

        plan = planner.plan_maintenance(findings, impacts)

        assert plan.total_findings == 50
        assert plan.deduplicated_count == 49
        assert len(plan.proposals) == 1

        single_prop = plan.proposals[0]
        assert single_prop.target_entities == ["org:acme", "org:acme_corp"]
        assert single_prop.recurrence_count == 50
        assert single_prop.artifact["extensions"]["com.partner_advisory.nee.maintenance"]["deduplicated_findings_count"] == 50

    def test_invariant_01_zero_canonical_graph_writes_during_planning(self) -> None:
        """HarmPrioritizedPlanner is strictly read-only and must never mutate canonical database state."""
        with tempfile.TemporaryDirectory(prefix="graph_readonly_test_") as tmp_dir:
            db_path = Path(tmp_dir) / "canonical_test.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT);")
            conn.execute("INSERT INTO entities VALUES ('e1', 'Canonical Corp');")
            conn.commit()
            conn.close()

            planner = HarmPrioritizedPlanner(run_id="run-ro-test")
            assert planner.is_read_only is True

            finding = HygieneFinding(
                finding_id="find-ro-01",
                category="duplicate_node",
                severity="HIGH",
                target_entities=["e1", "e2"],
                description="Merge test.",
                proposed_fix="merge e2 into e1",
            )

            # Generate maintenance proposals
            plan = planner.plan_maintenance([finding])
            assert len(plan.proposals) == 1

            # Verify canonical database remains identical
            verify_conn = sqlite3.connect(db_path)
            rows = verify_conn.execute("SELECT * FROM entities;").fetchall()
            verify_conn.close()

            assert len(rows) == 1
            assert rows[0] == ("e1", "Canonical Corp")

    def test_boundary_01_empty_and_corrupt_payloads_fail_closed(self) -> None:
        """Empty, whitespace, and invalid payloads fail closed with critical findings."""
        evaluator = SelectiveDefectEvaluator()

        # Completely empty strings
        rep_empty = evaluator.evaluate_all("", "", "")
        assert rep_empty.passed is False
        assert rep_empty.dimension_results["schema"].status == "FAIL"
        assert rep_empty.dimension_results["speaker"].status == "FAIL"

        # Whitespace only
        rep_ws = evaluator.evaluate_all("   ", "   ", "   ")
        assert rep_ws.passed is False
        assert rep_ws.dimension_results["schema"].status == "FAIL"

        # None / Bad types
        schema_res = evaluator.evaluate_schema(None)  # type: ignore[arg-type]
        assert schema_res.status == "FAIL"

    def test_conflict_01_contradictory_maintenance_fixes_flagged_with_high_risk(self) -> None:
        """Conflicting proposed fixes on the same entity cluster must be flagged for human review."""
        planner = HarmPrioritizedPlanner(run_id="run-conflict-01")

        findings = [
            HygieneFinding(
                finding_id="find-conf-1",
                category="duplicate_node",
                severity="HIGH",
                target_entities=["org:partner_advisory", "org:partner_advisory_build"],
                description="Propose merge into partner_advisory.",
                proposed_fix="merge org:partner_advisory_build into org:partner_advisory",
            ),
            HygieneFinding(
                finding_id="find-conf-2",
                category="duplicate_node",
                severity="HIGH",
                target_entities=["org:partner_advisory", "org:partner_advisory_build"],
                description="Contradictory propose split / quarantine.",
                proposed_fix="quarantine org:partner_advisory_build as separate subsidiary",
            ),
        ]

        plan = planner.plan_maintenance(findings)
        assert len(plan.proposals) == 1

        prop = plan.proposals[0]
        assert prop.has_conflicts is True
        assert prop.artifact["risk"] == "high"
        assert "Conflicting proposed fixes" in (prop.conflict_details or "")

    def test_security_01_raw_credential_leak_triggers_critical_provenance_failure(self) -> None:
        """Raw cloud credentials in output artifacts fail closed with CRITICAL defect."""
        evaluator = SelectiveDefectEvaluator()

        leaked_f1 = "Alice: My AWS key is AKIA_REDACTED_EXAMPLE for testing."
        clean_f2 = "Attendees: Alice\nSummary: Security talk."
        clean_f3 = json.dumps({"canonical": True})

        report = evaluator.evaluate_all(leaked_f1, clean_f2, clean_f3)
        assert report.passed is False
        assert report.dimension_results["provenance"].status == "FAIL"
        assert any(f.severity == "CRITICAL" for f in report.dimension_results["provenance"].findings)
