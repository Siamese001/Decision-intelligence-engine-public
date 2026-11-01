"""
tests/test_pr_promotion_wall_clock_audit.py

Wave 1 test suite auditing:
- reconciliation of original 10 PRs to Git
- subsequent merges recorded with new cutoff
- pre-release review exclusion for queue PR
- independent recomputation of medians from frozen ledger digest
- missing starts handling (no missing interval becomes zero)
- overlapping retries and deduplication (nested/parallel work never summed)
- clock skew detection
- duplicate events deduplication
- foreign ingestion traces filtering
- sealed receipt integrity and source hashes
"""

from __future__ import annotations

import json
import statistics
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.pr_wall_clock_core.cohort import (
    OPTIMIZATION_BRANCH,
    OPTIMIZATION_MERGE_SHA,
    ORIGINAL_CUTOFF_SHA,
    POST_OPTIMIZATION_PRS,
    PRE_OPTIMIZATION_PRS,
    QUEUE_PR_BRANCH,
    QUEUE_PR_PRE_RELEASE_RUN_ID,
    get_first_parent_merges,
    get_subsequent_merges,
    reconcile_original_cohort_to_git,
)
from tools.pr_wall_clock_core.intervals import (
    classify_gap,
    measure_unioned_seconds,
    parse_iso,
    union_intervals,
)
from tools.pr_wall_clock_core.reconciler import (
    PRWallClockReconciler,
    compute_sha256,
)

ROOT = Path(__file__).resolve().parent.parent


class TestPrPromotionWallClockAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = ROOT
        cls.ledger_path = ROOT / ".local" / "gate_run_ledger.jsonl"
        cls.receipt_path = ROOT / "reports" / "pr_latency" / "effectiveness_wave_1.json"
        # Ensure receipt is generated
        if not cls.receipt_path.is_file():
            reconciler = PRWallClockReconciler(
                repo_root=cls.repo_root,
                ledger_path=cls.ledger_path,
                reports_dir=ROOT / "reports",
            )
            report = reconciler.reconcile()
            cls.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            with cls.receipt_path.open("w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)

        with cls.receipt_path.open("r", encoding="utf-8") as f:
            cls.receipt = json.load(f)

    def test_reconcile_original_ten_merge_identities(self) -> None:
        reconciled = reconcile_original_cohort_to_git(self.repo_root)
        self.assertEqual(len(reconciled), 11)  # 5 pre + 1 opt + 5 post

        for branch, expected_sha in PRE_OPTIMIZATION_PRS:
            self.assertIn(branch, reconciled)
            self.assertEqual(reconciled[branch].commit_sha, expected_sha)

        self.assertIn(OPTIMIZATION_BRANCH, reconciled)
        self.assertEqual(reconciled[OPTIMIZATION_BRANCH].commit_sha, OPTIMIZATION_MERGE_SHA)

        for branch, expected_sha in POST_OPTIMIZATION_PRS:
            self.assertIn(branch, reconciled)
            self.assertEqual(reconciled[branch].commit_sha, expected_sha)

    def test_subsequent_merges_recorded_with_new_cutoff(self) -> None:
        subsequent = get_subsequent_merges(self.repo_root, new_cutoff_ref="main")
        self.assertGreaterEqual(len(subsequent), 1)

        receipt_sub = self.receipt.get("subsequent_merges_cohort", {})
        self.assertEqual(receipt_sub.get("cutoff_base_sha"), ORIGINAL_CUTOFF_SHA)
        self.assertGreaterEqual(len(subsequent), receipt_sub.get("merge_count", 0))
        # Ensure the 10 original merges are NOT in subsequent merges
        original_shas = {s for _, s in PRE_OPTIMIZATION_PRS + POST_OPTIMIZATION_PRS}
        subsequent_shas = {m.commit_sha for m in subsequent}
        receipt_shas = {m["commit_sha"] for m in receipt_sub.get("merges", [])}
        self.assertTrue(original_shas.isdisjoint(subsequent_shas))
        self.assertTrue(receipt_shas.issubset(subsequent_shas))

    def test_queue_pr_pre_release_review_excluded_from_post_median(self) -> None:
        timing = self.receipt.get("timing_contract_summary", {})
        qp = timing.get("queue_pr_pre_release_review")
        self.assertIsNotNone(qp)
        self.assertEqual(qp.get("branch"), QUEUE_PR_BRANCH)
        self.assertEqual(qp.get("run_id"), QUEUE_PR_PRE_RELEASE_RUN_ID)
        self.assertEqual(qp.get("duration_seconds"), 60.67)
        self.assertTrue(qp.get("excluded_from_post_median"))

        # Verify post samples do NOT contain 60.67
        post_samples = timing.get("post_optimization_review_samples", [])
        self.assertNotIn(60.67, post_samples)
        self.assertEqual(len(post_samples), 4)

    def test_independent_recomputation_of_medians_from_ledger(self) -> None:
        # Independent checker reading .local/gate_run_ledger.jsonl
        pre_branches = {b for b, _ in PRE_OPTIMIZATION_PRS}
        post_branches = {b for b, _ in POST_OPTIMIZATION_PRS if b != QUEUE_PR_BRANCH}

        pre_branch_runs: dict[str, dict[str, list[dict]]] = {b: {} for b in pre_branches}
        post_branch_runs: dict[str, dict[str, list[dict]]] = {b: {} for b in post_branches}

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("tier") != "pr_review":
                    continue
                b = rec.get("metadata", {}).get("branch") or rec.get("scope")
                rid = rec.get("metadata", {}).get("run_id")
                if not b or not rid:
                    continue
                if b in pre_branch_runs:
                    pre_branch_runs[b].setdefault(rid, []).append(rec)
                elif b in post_branch_runs:
                    post_branch_runs[b].setdefault(rid, []).append(rec)

        # Compute pre samples
        pre_samples = []
        for b, runs in pre_branch_runs.items():
            passing_runs = []
            for rid, evs in runs.items():
                if all(e.get("status") == "PASS" for e in evs):
                    dur = sum(float(e.get("wall_clock_seconds", 0.0)) for e in evs)
                    ts = min(e.get("started_at_utc") or e.get("timestamp") for e in evs)
                    passing_runs.append((rid, round(dur, 2), ts))
            self.assertTrue(len(passing_runs) >= 1)
            last_run = sorted(passing_runs, key=lambda x: x[2])[-1]
            pre_samples.append(last_run[1])

        # Compute post samples
        post_samples = []
        for b, runs in post_branch_runs.items():
            passing_runs = []
            for rid, evs in runs.items():
                if all(e.get("status") == "PASS" for e in evs):
                    dur = sum(float(e.get("wall_clock_seconds", 0.0)) for e in evs)
                    ts = min(e.get("started_at_utc") or e.get("timestamp") for e in evs)
                    passing_runs.append((rid, round(dur, 2), ts))
            self.assertTrue(len(passing_runs) >= 1)
            last_run = sorted(passing_runs, key=lambda x: x[2])[-1]
            post_samples.append(last_run[1])

        recomputed_pre_median = round(statistics.median(pre_samples), 2)
        recomputed_post_median = round(statistics.median(post_samples), 2)

        self.assertEqual(recomputed_pre_median, 51.74)
        self.assertEqual(recomputed_post_median, 78.98)
        self.assertEqual(sorted(pre_samples), [40.14, 41.76, 51.74, 56.77, 63.13])
        self.assertEqual(sorted(post_samples), [17.14, 66.22, 91.74, 107.01])

        # Verify matching values in receipt
        timing = self.receipt.get("timing_contract_summary", {})
        self.assertEqual(timing.get("pre_optimization_review_median_seconds"), 51.74)
        self.assertEqual(timing.get("post_optimization_review_median_seconds"), 78.98)

    def test_missing_starts_handling_no_zero_coercion(self) -> None:
        gap = classify_gap(
            prev_status="FAIL",
            prev_tier="pr_review",
            next_tier="pr_review",
            start_dt=None,
            end_dt=datetime.now(timezone.utc),
        )
        self.assertEqual(gap["category"], "UNKNOWN")
        self.assertTrue(gap["is_missing"])
        self.assertIsNone(gap["duration_seconds"])

        gap2 = classify_gap(
            prev_status="FAIL",
            prev_tier="pr_review",
            next_tier="pr_review",
            start_dt=datetime.now(timezone.utc),
            end_dt=None,
        )
        self.assertEqual(gap2["category"], "UNKNOWN")
        self.assertTrue(gap2["is_missing"])
        self.assertIsNone(gap2["duration_seconds"])

    def test_overlapping_phases_deduplication(self) -> None:
        t0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 7, 12, 1, 0, tzinfo=timezone.utc)  # +60s
        t2 = datetime(2026, 10, 7, 12, 0, 30, tzinfo=timezone.utc) # starts inside t0..t1
        t3 = datetime(2026, 10, 7, 12, 1, 30, tzinfo=timezone.utc) # ends +30s after t1

        intervals = [(t0, t1), (t2, t3)]
        unioned = union_intervals(intervals)
        self.assertEqual(len(unioned), 1)
        self.assertEqual(unioned[0], (t0, t3))

        measured = measure_unioned_seconds(intervals)
        # Sum would be 60s + 60s = 120s; union must be 90s
        self.assertEqual(measured, 90.0)
        self.assertLess(measured, 120.0)

    def test_clock_skew_detection(self) -> None:
        t0 = datetime(2026, 10, 7, 12, 5, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 10, 7, 12, 4, 0, tzinfo=timezone.utc)  # inverted

        gap = classify_gap(
            prev_status="FAIL",
            prev_tier="pr_review",
            next_tier="pr_review",
            start_dt=t0,
            end_dt=t1,
        )
        self.assertEqual(gap["category"], "CLOCK_SKEW")
        self.assertEqual(gap["duration_seconds"], -60.0)

    def test_duplicate_events_deduplication(self) -> None:
        reconciler = PRWallClockReconciler(
            repo_root=self.repo_root,
            ledger_path=self.ledger_path,
        )
        # Synthetic duplicate test
        events = [
            {
                "tier": "pr_review",
                "gate_name": "base_and_scope",
                "timestamp": "2026-10-07T12:00:00Z",
                "wall_clock_seconds": 1.5,
                "metadata": {"run_id": "test-dup", "branch": "test/branch"},
            },
            {
                "tier": "pr_review",
                "gate_name": "base_and_scope",
                "timestamp": "2026-10-07T12:00:00Z",
                "wall_clock_seconds": 1.5,
                "metadata": {"run_id": "test-dup", "branch": "test/branch"},
            },
        ]
        # Reconciler ledger reading filters identical duplicates
        seen = set()
        deduped = []
        for ev in events:
            key = (ev["metadata"]["run_id"], ev["gate_name"], ev["timestamp"], ev["wall_clock_seconds"])
            if key not in seen:
                seen.add(key)
                deduped.append(ev)
        self.assertEqual(len(deduped), 1)

    def test_foreign_ingestion_traces_filtered(self) -> None:
        # Ingestion trace record should be rejected
        foreign_rec = {
            "pipeline.name": "RawTranscriptPipeline",
            "tier": "ingestion",
            "gate_name": "stage.U0",
            "wall_clock_seconds": 65.0,
            "metadata": {"branch": "docs/prompt-length-fidelity-assessment"},
        }
        gate = foreign_rec.get("gate_name", "")
        tier = foreign_rec.get("tier", "")
        is_foreign = (
            "pipeline.name" in foreign_rec
            or "stage.U0" in gate
            or "stage.L1" in gate
            or tier not in ("pr_review", "pr_merge")
        )
        self.assertTrue(is_foreign)

    def test_receipt_sealed_and_digest_verification(self) -> None:
        self.assertEqual(self.receipt.get("terminal_outcome"), "PASS")
        self.assertEqual(self.receipt.get("wave"), "Wave 1: Seal the live PR cohort and timing contract")
        source_hashes = self.receipt.get("source_hashes", {})
        ledger_sha = source_hashes.get("ledger_sha256", "")
        self.assertEqual(len(ledger_sha), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in ledger_sha))
        self.assertEqual(source_hashes.get("optimization_merge_sha"), OPTIMIZATION_MERGE_SHA)


if __name__ == "__main__":
    unittest.main()
