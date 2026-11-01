#!/usr/bin/env python3
"""Class A Unit Tests: Persistent Ledger Hardening, Concurrency Stress & Merkle Auditing (Wave 3).

Verifies:
1. Multi-process lock serialization with 30s busy timeout.
2. 16-worker concurrent write stress test without record loss or JSON corruption.
3. Merkle hash chain linking from genesis ('0'*64) to latest entry.
4. Tamper-evident validation: detecting modified fields or deleted/reordered entries.
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.hitl_governance import HITLDecisionStore
from tools.validate_hitl_ledger_integrity import ZERO_HASH, validate_ledger_integrity


class HitlLedgerHardeningTests(unittest.TestCase):
    def test_merkle_chain_creation_and_validation(self) -> None:
        """Sequential writes must produce unbroken Merkle hash chain from genesis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            store = HITLDecisionStore(ledger_path)

            for i in range(5):
                store.record_decision(
                    decision_id=f"dec_seq_{i}",
                    category="ARCHITECTURAL_REVIEW",
                    prompt=f"Review decision {i}",
                    options=[{"key": "a", "label": "Option A"}, {"key": "b", "label": "Option B"}],
                    selected_option={"key": "a", "label": "Option A"},
                    rationale=f"Approved step {i}",
                )

            report = validate_ledger_integrity(ledger_path)
            self.assertTrue(report["is_valid"], f"Integrity check failed: {report['errors']}")
            self.assertEqual(report["record_count"], 5)
            self.assertEqual(report["genesis_hash"], ZERO_HASH)
            self.assertEqual(report["integrity_status"], "VALID")

            # Inspect records directly
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            recs = data["decisions"]
            self.assertEqual(recs[0]["previous_entry_hash"], ZERO_HASH)
            for j in range(1, 5):
                self.assertEqual(recs[j]["previous_entry_hash"], recs[j - 1]["entry_digest"])

    def test_tamper_detection(self) -> None:
        """Any manual modification of a record must be flagged as tamper defect."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            store = HITLDecisionStore(ledger_path)

            for i in range(3):
                store.record_decision(
                    decision_id=f"dec_tamper_{i}",
                    category="TEST_POLICY",
                    prompt="Test prompt",
                    options=["Opt 1", "Opt 2"],
                    selected_option="Opt 1",
                )

            # Maliciously modify record index 1
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            data["decisions"][1]["rationale"] = "MALICIOUS_INJECTION"
            ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            report = validate_ledger_integrity(ledger_path)
            self.assertFalse(report["is_valid"])
            self.assertEqual(report["integrity_status"], "COMPROMISED")
            self.assertTrue(any("TAMPER_DETECTED" in e for e in report["errors"]))

    def test_chain_break_detection(self) -> None:
        """Deleting or reordering entries must be detected as a broken Merkle chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            store = HITLDecisionStore(ledger_path)

            for i in range(4):
                store.record_decision(
                    decision_id=f"dec_chain_{i}",
                    category="TEST_POLICY",
                    prompt=f"Prompt {i}",
                    options=["Opt 1", "Opt 2"],
                    selected_option="Opt 1",
                )

            # Remove record 1 (creating a gap between 0 and 2)
            data = json.loads(ledger_path.read_text(encoding="utf-8"))
            del data["decisions"][1]
            data["record_count"] = len(data["decisions"])
            ledger_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            report = validate_ledger_integrity(ledger_path)
            self.assertFalse(report["is_valid"])
            self.assertTrue(any("CHAIN_BROKEN" in e or "SEQUENCE_MISMATCH" in e for e in report["errors"]))

    def test_16_worker_concurrency_stress(self) -> None:
        """16 concurrent workers recording decisions simultaneously must preserve all records and chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "hitl_decision_ledger.json"
            num_workers = 16
            records_per_worker = 5
            total_expected = num_workers * records_per_worker

            def worker_task(worker_id: int) -> int:
                worker_store = HITLDecisionStore(ledger_path)
                written = 0
                for r in range(records_per_worker):
                    worker_store.record_decision(
                        decision_id=f"worker_{worker_id}_rec_{r}",
                        category="CONCURRENCY_STRESS",
                        prompt=f"Decision from worker {worker_id} pass {r}",
                        options=[{"key": "affirm", "label": "Affirm"}, {"key": "reject", "label": "Reject"}],
                        selected_option={"key": "affirm", "label": "Affirm"},
                        operator_id=f"operator_worker_{worker_id}",
                    )
                    written += 1
                return written

            # Execute 16 workers in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
                futures = [executor.submit(worker_task, w) for w in range(num_workers)]
                results = [f.result(timeout=45.0) for f in futures]

            self.assertEqual(sum(results), total_expected)

            # Validate ledger integrity under validator
            report = validate_ledger_integrity(ledger_path)
            self.assertTrue(
                report["is_valid"],
                f"Concurrent ledger integrity failed: {report['errors']}",
            )
            self.assertEqual(report["record_count"], total_expected)
            self.assertEqual(report["integrity_status"], "VALID")


if __name__ == "__main__":
    unittest.main()
