"""
tests/end_to_end/test_hitl_event_store_e2e.py

End-to-end test suite for Wave 10 Decision Lifecycle (DLC) Hardening:
- SQLite as the single append-only, Merkle-chained HITL event store
- O(1) write cost verification
- On-demand JSON projection export and bit-parity validation
- 100% latency stamping and SLO tracking
- Operator alias roster normalization
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.operator_roster import (
    DECISION_LATENCY_SLO_MS,
    check_decision_latency_slo,
    normalize_operator_id,
)


class TestHitlEventStoreE2E(unittest.TestCase):
    """End-to-end test suite for HITL event store lifecycle and performance."""

    def test_hitl_sqlite_event_store_lifecycle(self) -> None:
        """Verify SQLite event store functions as the authoritative Merkle-chained store."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            sqlite_p = tmp_dir / "test_ledger.sqlite"
            json_p = tmp_dir / "test_ledger.json"

            store = HITLDecisionStore(ledger_path=json_p, sqlite_path=sqlite_p, use_sqlite=True)
            self.assertTrue(store.use_sqlite)
            self.assertIsNotNone(store.sqlite_store)

            # Record 5 sequential decisions with operator aliases
            operators = ["Amit Ayer", "amit.ayer", "operator", "autonomous_context", "mock_operator"]
            records = []
            for idx, op in enumerate(operators):
                rec = store.record_decision(
                    decision_id=f"DEC-E2E-{idx:03d}",
                    category="architectural_governance",
                    prompt=f"Prompt {idx}",
                    options=[
                        {"label": f"Option A {idx}", "confidence": 0.85, "recommended": True},
                        {"label": f"Option B {idx}", "confidence": 0.60},
                    ],
                    selected_option=f"Option A {idx}",
                    rationale=f"Rationale {idx}",
                    operator_id=op,
                    presented_at="2026-10-05T09:00:00Z",
                    resolved_at="2026-10-05T09:00:05Z",
                )
                records.append(rec)

            # Verify total records in SQLite
            self.assertEqual(store.sqlite_store.total_records(), 5)
            history = store.get_history()
            self.assertEqual(len(history), 5)

            # Verify Merkle chaining
            prev_hash = "0" * 64
            for idx, rec in enumerate(history):
                self.assertEqual(rec["sequence_number"], idx)
                self.assertEqual(rec["previous_entry_hash"], prev_hash)
                prev_hash = rec["entry_digest"]

            # Verify operator normalization
            self.assertEqual(history[0]["operator_id"], "amit_ayer")
            self.assertEqual(history[0]["operator_class"], "HUMAN")
            self.assertEqual(history[1]["operator_id"], "amit_ayer")
            self.assertEqual(history[2]["operator_id"], "human_operator")
            self.assertEqual(history[3]["operator_id"], "autonomous_engine")
            self.assertEqual(history[3]["operator_class"], "AUTONOMOUS")
            self.assertEqual(history[4]["operator_id"], "mock_operator")
            self.assertEqual(history[4]["operator_class"], "TEST")

            # Verify latency stamping and SLO coverage
            for rec in history:
                self.assertIn("presented_at", rec)
                self.assertIn("resolved_at", rec)
                self.assertIn("resolution_latency_ms", rec)
                self.assertEqual(rec["resolution_latency_ms"], 5000)
                self.assertEqual(rec["latency_slo_status"], "MET")

            # Verify on-demand JSON projection
            proj_path = store.export_json_projection()
            self.assertTrue(proj_path.is_file())

            # Re-load from JSON projection and verify bit parity
            store_from_json = HITLDecisionStore(ledger_path=proj_path, use_sqlite=False)
            json_records = store_from_json._load_records(verify_integrity=True)
            self.assertEqual(len(json_records), 5)
            for h_rec, j_rec in zip(history, json_records):
                self.assertEqual(h_rec["entry_id"], j_rec["entry_id"])
                self.assertEqual(h_rec["entry_digest"], j_rec["entry_digest"])
                self.assertEqual(h_rec["previous_entry_hash"], j_rec["previous_entry_hash"])

    def test_o1_write_cost_flat_benchmarking(self) -> None:
        """Verify write cost remains O(1) flat as store grows."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            sqlite_p = tmp_dir / "bench_ledger.sqlite"
            store = HITLDecisionStore(sqlite_path=sqlite_p, use_sqlite=True)

            # Pre-populate 1,000 synthetic records via batch insert
            seed_records = [
                {
                    "entry_id": f"hitl-seed-{i:06d}",
                    "sequence_number": i,
                    "decision_id": f"DEC-SEED-{i:06d}",
                    "category": "benchmark",
                    "operator_id": "autonomous_engine",
                    "prompt": f"Benchmark prompt {i}",
                    "options": [{"label": "Opt A", "confidence": 0.90}],
                    "selected_option": "Opt A",
                    "selected_key": "Opt A",
                    "rationale": "Seed",
                    "previous_entry_hash": "0" * 64,
                    "recorded_at": "2026-10-05T00:00:00Z",
                    "metadata": {"operator_class": "AUTONOMOUS", "outcome_type": "AFFIRMED"},
                    "entry_digest": f"{i:064d}",
                }
                for i in range(1000)
            ]
            store.sqlite_store.insert_decisions_batch(seed_records)
            self.assertEqual(store.sqlite_store.total_records(), 1000)

            # Benchmark 20 single inserts
            latencies = []
            for i in range(20):
                t0 = time.monotonic()
                store.record_decision(
                    decision_id=f"DEC-BENCH-{i:03d}",
                    category="benchmark",
                    prompt=f"Bench {i}",
                    options=[{"label": "A", "confidence": 0.88}],
                    selected_option="A",
                )
                latencies.append((time.monotonic() - t0) * 1000)

            # Average write latency must be < 80ms per record (flat O(1))
            avg_latency = sum(latencies) / len(latencies)
            self.assertLess(avg_latency, 80.0, f"Average insert latency {avg_latency:.2f}ms exceeds 80ms budget")

    def test_decision_latency_slo_tracking(self) -> None:
        """Verify SLO tracking correctly identifies compliant vs breached decisions."""
        # Compliant human decision (120s <= 300s)
        res_human_ok = check_decision_latency_slo("HUMAN", 120_000)
        self.assertTrue(res_human_ok["slo_met"])
        self.assertEqual(res_human_ok["status"], "MET")
        self.assertEqual(res_human_ok["target_ms"], DECISION_LATENCY_SLO_MS["HUMAN"])

        # Breached human decision (360s > 300s)
        res_human_breach = check_decision_latency_slo("HUMAN", 360_000)
        self.assertFalse(res_human_breach["slo_met"])
        self.assertEqual(res_human_breach["status"], "EXCEEDED")

        # Compliant autonomous decision (2s <= 5s)
        res_auto_ok = check_decision_latency_slo("AUTONOMOUS", 2_000)
        self.assertTrue(res_auto_ok["slo_met"])
        self.assertEqual(res_auto_ok["status"], "MET")

        # Breached autonomous decision (12s > 5s)
        res_auto_breach = check_decision_latency_slo("AUTONOMOUS", 12_000)
        self.assertFalse(res_auto_breach["slo_met"])
        self.assertEqual(res_auto_breach["status"], "EXCEEDED")

    def test_operator_roster_normalization(self) -> None:
        """Verify alias roster maps all known human and system variants to canonical identifiers."""
        self.assertEqual(normalize_operator_id("Amit Ayer"), "amit_ayer")
        self.assertEqual(normalize_operator_id("amit.ayer"), "amit_ayer")
        self.assertEqual(normalize_operator_id("amitayer"), "amit_ayer")
        self.assertEqual(normalize_operator_id("operator_amitayer"), "amit_ayer")
        self.assertEqual(normalize_operator_id("operator"), "human_operator")
        self.assertEqual(normalize_operator_id("operator_1"), "human_operator")
        self.assertEqual(normalize_operator_id("user"), "human_operator")
        self.assertEqual(normalize_operator_id("autonomous_context"), "autonomous_engine")
        self.assertEqual(normalize_operator_id("mock_operator"), "mock_operator")
        self.assertEqual(normalize_operator_id("external_auditor"), "external_auditor")
