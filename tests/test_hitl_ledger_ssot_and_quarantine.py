"""
tests/test_hitl_ledger_ssot_and_quarantine.py

Unit, property, and integration tests for Wave 1:
1. Reader/writer path resolution parity between resolve_hitl_ledger_path and resolve_ledger_paths.
2. Dual-key JSON schema compatibility (decisions and operator_decisions).
3. Autonomous record quarantine: non-surfaced autonomous stage checks do not pollute the human ledger.
4. Autonomous noise filtration during Bayesian prior accumulation.
5. Reconcile ledgers deduplication, noise filtering, and parity verification.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.hitl_core.envelope import HitlRequirementEnvelope, create_hitl_envelope
from dual_pipeline.hitl_governance_core.bayesian import (
    _accumulate_historical_weights,
    _compute_record_weight,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    resolve_ledger_paths,
    sync_sqlite_to_json_export,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.telemetry import resolve_hitl_ledger_path
from storage.connection import create_sqlite_conn
from tools.reconcile_hitl_ledgers import reconcile_ledgers


class HitlLedgerSSOTAndQuarantineTests(unittest.TestCase):
    """Verifies SSOT path unification, dual-key compatibility, and autonomous quarantine."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.sqlite_path, self.json_path = resolve_ledger_paths(repo_root=self.root)
        self.store = HITLDecisionStore(ledger_path=self.json_path, sqlite_path=self.sqlite_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_reader_writer_path_resolution_parity(self) -> None:
        """Verify resolve_hitl_ledger_path and resolve_ledger_paths return identical paths."""
        reader_json = resolve_hitl_ledger_path(repo_root=self.root)
        writer_sqlite, writer_json = resolve_ledger_paths(repo_root=self.root)

        self.assertEqual(reader_json, writer_json)
        self.assertEqual(writer_sqlite, self.root / "hitl_decision_ledger.sqlite")
        self.assertEqual(writer_json, self.root / "hitl_decision_ledger.json")
        self.assertTrue(str(writer_sqlite).startswith(str(self.root)))
        self.assertTrue(str(writer_json).startswith(str(self.root)))

        # In active workspace default (repo_root=None)
        default_reader_json = resolve_hitl_ledger_path()
        default_writer_sqlite, default_writer_json = resolve_ledger_paths()
        self.assertEqual(default_reader_json, default_writer_json)
        self.assertEqual(default_writer_sqlite.with_suffix(".json"), default_writer_json)

    def test_schema_parity_operator_decisions_and_decisions(self) -> None:
        """Verify HITLDecisionStore correctly ingests operator_decisions, decisions, or both."""
        # 1. Store loads schema 1.2.0 with only operator_decisions
        legacy_dir = self.root / "fixtures"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        fixture_json_120 = legacy_dir / "ledger_120.json"
        payload_120 = {
            "schema_version": "1.2.0",
            "store_of_record": "hitl_decision_ledger.sqlite",
            "operator_decisions": [
                {
                    "entry_id": "ent-120-1",
                    "sequence_number": 0,
                    "decision_id": "dec-120-1",
                    "operator": "human_architect",
                    "topic": "Choose database engine",
                    "category": "database_selection",
                    "options": [{"key": "sqlite", "label": "SQLite"}],
                    "resolution": {"key": "sqlite", "label": "SQLite"},
                    "selected_key": "sqlite",
                    "timestamp": "2026-10-14T10:00:00Z",
                    "entry_digest": "a" * 64,
                    "previous_entry_hash": "0" * 64,
                }
            ],
        }
        fixture_json_120.write_text(json.dumps(payload_120), encoding="utf-8")

        store_120 = HITLDecisionStore(ledger_path=fixture_json_120, use_sqlite=False)
        recs_120 = store_120._load_records_from_json(verify_integrity=False)
        self.assertEqual(len(recs_120), 1)
        self.assertEqual(recs_120[0]["entry_id"], "ent-120-1")
        self.assertEqual(recs_120[0]["operator_id"], "human_architect")
        self.assertEqual(recs_120[0]["prompt"], "Choose database engine")
        self.assertEqual(recs_120[0]["selected_option"]["key"], "sqlite")

        # 2. Store loads schema 1.1.0 with only decisions
        fixture_json_110 = legacy_dir / "ledger_110.json"
        payload_110 = {
            "schema_version": "1.1.0",
            "decisions": [
                {
                    "entry_id": "ent-110-1",
                    "sequence_number": 0,
                    "decision_id": "dec-110-1",
                    "operator_id": "human_operator",
                    "prompt": "Approve release candidate",
                    "category": "release_approval",
                    "options": [{"key": "approve", "label": "Approve"}],
                    "selected_option": {"key": "approve", "label": "Approve"},
                    "selected_key": "approve",
                    "recorded_at": "2026-10-14T10:05:00Z",
                    "entry_digest": "b" * 64,
                    "previous_entry_hash": "0" * 64,
                }
            ],
        }
        fixture_json_110.write_text(json.dumps(payload_110), encoding="utf-8")

        store_110 = HITLDecisionStore(ledger_path=fixture_json_110, use_sqlite=False)
        recs_110 = store_110._load_records_from_json(verify_integrity=False)
        self.assertEqual(len(recs_110), 1)
        self.assertEqual(recs_110[0]["entry_id"], "ent-110-1")

        # 3. Store merges both keys without duplication
        fixture_json_both = legacy_dir / "ledger_both.json"
        payload_both = {
            "schema_version": "1.2.0",
            "operator_decisions": payload_120["operator_decisions"],
            "decisions": payload_120["operator_decisions"] + payload_110["decisions"],
        }
        fixture_json_both.write_text(json.dumps(payload_both), encoding="utf-8")

        store_both = HITLDecisionStore(ledger_path=fixture_json_both, use_sqlite=False)
        recs_both = store_both._load_records_from_json(verify_integrity=False)
        self.assertEqual(len(recs_both), 2)
        entry_ids = {r["entry_id"] for r in recs_both}
        self.assertEqual(entry_ids, {"ent-120-1", "ent-110-1"})

    def test_sync_sqlite_to_json_export_populates_both_keys(self) -> None:
        """Verify sync_sqlite_to_json_export generates JSON with both operator_decisions and decisions."""
        self.store.record_decision(
            decision_id="dec-sync-1",
            category="routing",
            prompt="Route candidate to graph?",
            options=[
                {"label": "Approve", "confidence_score": 0.90},
                {"label": "Reject", "confidence_score": 0.10},
            ],
            selected_option={"label": "Approve", "confidence_score": 0.90},
            operator_id="operator_test",
        )

        sync_sqlite_to_json_export(self.sqlite_path, self.json_path)

        data = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "1.2.0")
        self.assertEqual(data["total_records"], 1)
        self.assertEqual(len(data["operator_decisions"]), 1)
        self.assertEqual(len(data["decisions"]), 1)
        self.assertEqual(data["operator_decisions"][0]["decision_id"], "dec-sync-1")
        self.assertEqual(data["decisions"][0]["decision_id"], "dec-sync-1")

    def test_autonomous_stage_quarantine(self) -> None:
        """Verify non-surfaced autonomous stage checks do not write to human ledger."""
        options = [
            {"key": "silence_gap", "label": "Silence Gap", "confidence": 0.95},
            {"key": "speaker_transition", "label": "Speaker Transition", "confidence": 0.05},
        ]
        # Margin = 0.90 >> threshold (0.30) -> should_surface is False
        envelope = create_hitl_envelope(
            category="silence_gap_classification",
            domain="transcript_intake",
            question="Classify audio gap at [00:01:20]",
            options=options,
            store=self.store,
            threshold=0.30,
        )
        self.assertFalse(envelope.should_surface)

        # 1. Resolve autonomously without persist_autonomous flag -> Quarantined
        receipt = envelope.resolve_and_persist(
            selection="silence_gap",
            operator_id="autonomous_context",
            store=self.store,
        )
        self.assertEqual(receipt["status"], "RESOLVED")
        self.assertIn("receipt_digest", receipt)

        # Confirm store history has 0 records
        records = self.store.get_history(category="silence_gap_classification")
        self.assertEqual(len(records), 0)

        # Verify raw SQLite table has 0 rows
        conn = create_sqlite_conn(self.sqlite_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM hitl_decisions")
        count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

        # 2. Resolve with persist_autonomous=True -> Explicitly allowed
        receipt_explicit = envelope.resolve_and_persist(
            selection="silence_gap",
            operator_id="autonomous_context",
            store=self.store,
            persist_autonomous=True,
        )
        self.assertEqual(receipt_explicit["status"], "RESOLVED")
        self.assertEqual(len(self.store.get_history(category="silence_gap_classification")), 1)

        # 3. Surfaced human decision -> Persists normally
        surfaced_envelope = create_hitl_envelope(
            category="speaker_attribution",
            domain="transcript_intake",
            question="Disambiguate Speaker 2",
            options=[
                {"key": "alice", "label": "Alice", "confidence": 0.52},
                {"key": "bob", "label": "Bob", "confidence": 0.48},
            ],
            store=self.store,
            threshold=0.30,
        )
        self.assertTrue(surfaced_envelope.should_surface)
        surfaced_envelope.resolve_and_persist(
            selection="alice",
            operator_id="human_verifier",
            store=self.store,
        )
        records_human = self.store.get_history(category="speaker_attribution")
        self.assertEqual(len(records_human), 1)
        self.assertEqual(records_human[0]["operator_id"], "human_verifier")

    def test_bayesian_prior_filtering_autonomous_records(self) -> None:
        """Verify autonomous records lacking independent outcomes get 0 weight in Bayesian priors."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)

        human_rec = {
            "entry_id": "h-1",
            "category": "title_selection",
            "operator_id": "human_operator",
            "outcome_type": "AFFIRMED",
            "selected_key": "q3_strategy_review",
            "recorded_at": now.isoformat(),
        }
        auto_rec = {
            "entry_id": "a-1",
            "category": "title_selection",
            "operator_id": "autonomous_context",
            "outcome_type": "AUTONOMOUS",
            "selected_key": "q3_strategy_review",
            "recorded_at": now.isoformat(),
        }

        w_human = _compute_record_weight(human_rec, half_life_days=30.0, now=now)
        w_auto = _compute_record_weight(auto_rec, half_life_days=30.0, now=now)

        self.assertGreater(w_human, 0.0)
        self.assertEqual(w_auto, 0.0)

        aff_w, rej_w, aff_c, rej_c = _accumulate_historical_weights(
            [human_rec, auto_rec],
            category="title_selection",
            half_life_days=30.0,
            now=now,
        )
        # Affirmation count includes human, but autonomous did not add weight
        self.assertAlmostEqual(aff_w["q3_strategy_review"], w_human, places=3)

    def test_reconcile_ledgers_deduplication_and_filtering(self) -> None:
        """Verify reconcile_ledgers migrates human records while filtering un-surfaced autonomous noise."""
        sec_dir = self.root / "legacy_source"
        sec_dir.mkdir(parents=True, exist_ok=True)
        sec_json = sec_dir / "hitl_decision_ledger.json"

        # Construct legacy file with 1 human record, 1 duplicate human, and 2 autonomous silence gap classifications
        legacy_payload = {
            "schema_version": "1.1.0",
            "decisions": [
                {
                    "entry_id": "human-entry-001",
                    "sequence_number": 0,
                    "decision_id": "dec-human-1",
                    "category": "speaker_attribution",
                    "operator_id": "human_reviewer",
                    "prompt": "Assign speaker turn 4",
                    "options": [{"key": "alice", "label": "Alice"}],
                    "selected_option": {"key": "alice", "label": "Alice"},
                    "selected_key": "alice",
                    "recorded_at": "2026-10-14T09:00:00Z",
                    "entry_digest": "h1" + ("0" * 62),
                    "previous_entry_hash": "0" * 64,
                },
                {
                    "entry_id": "human-entry-001",  # duplicate
                    "sequence_number": 1,
                    "decision_id": "dec-human-1",
                    "category": "speaker_attribution",
                    "operator_id": "human_reviewer",
                    "prompt": "Assign speaker turn 4",
                    "options": [{"key": "alice", "label": "Alice"}],
                    "selected_option": {"key": "alice", "label": "Alice"},
                    "selected_key": "alice",
                    "recorded_at": "2026-10-14T09:00:00Z",
                    "entry_digest": "h1" + ("0" * 62),
                    "previous_entry_hash": "0" * 64,
                },
                {
                    "entry_id": "auto-noise-001",
                    "sequence_number": 2,
                    "decision_id": "dec-auto-1",
                    "category": "silence_gap_classification",
                    "operator_id": "autonomous_context",
                    "prompt": "Classify gap",
                    "options": [{"key": "gap", "label": "Gap"}],
                    "selected_option": {"key": "gap", "label": "Gap"},
                    "selected_key": "gap",
                    "recorded_at": "2026-10-14T09:01:00Z",
                    "entry_digest": "a1" + ("0" * 62),
                    "previous_entry_hash": "0" * 64,
                },
            ],
        }
        sec_json.write_text(json.dumps(legacy_payload), encoding="utf-8")

        auth_sqlite = self.root / "reconciled_ledger.sqlite"
        auth_json = self.root / "reconciled_ledger.json"
        receipt_path = self.root / "reconcile_receipt.json"

        receipt = reconcile_ledgers(
            authoritative_sqlite_path=auth_sqlite,
            authoritative_json_path=auth_json,
            secondary_json_path=sec_json,
            receipt_path=receipt_path,
        )

        self.assertEqual(receipt["status"], "PASS")
        self.assertTrue(receipt["parity_achieved"])
        self.assertEqual(receipt["divergence_count"], 0)
        # Exactly 1 human record should be inserted (duplicate and autonomous noise excluded)
        self.assertEqual(receipt["authoritative_sqlite_count"], 1)
        self.assertEqual(receipt["canonical_json_count"], 1)

        # Verify exported JSON has both operator_decisions and decisions
        reconciled_data = json.loads(auth_json.read_text(encoding="utf-8"))
        self.assertEqual(reconciled_data["schema_version"], "1.2.0")
        self.assertEqual(len(reconciled_data["operator_decisions"]), 1)
        self.assertEqual(len(reconciled_data["decisions"]), 1)
        self.assertEqual(reconciled_data["operator_decisions"][0]["entry_id"], "human-entry-001")


class HitlPropertyTests(unittest.TestCase):
    """Hypothesis property-based tests verifying hash-chaining and path invariants."""

    @settings(max_examples=10, deadline=15000)
    @given(
        st.lists(
            st.tuples(
                st.text(min_size=3, max_size=10, alphabet="abcdefghijklmnopqrstuvwxyz"),
                st.text(min_size=3, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz "),
                st.sampled_from(["opt_a", "opt_b"]),
            ),
            min_size=2,
            max_size=5,
        )
    )
    def test_property_merkle_chain_and_dual_key_export(self, decision_tuples: List[Any]) -> None:
        """Property: arbitrary sequential decisions maintain valid Merkle links and dual keys."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            s_path, j_path = resolve_ledger_paths(repo_root=root)
            store = HITLDecisionStore(ledger_path=j_path, sqlite_path=s_path)

            for idx, (cat, prompt, chosen_key) in enumerate(decision_tuples):
                store.record_decision(
                    decision_id=f"prop-dec-{idx}",
                    category=cat,
                    prompt=prompt,
                    options=[
                        {"key": "opt_a", "label": "Option A", "confidence": 0.80},
                        {"key": "opt_b", "label": "Option B", "confidence": 0.60},
                    ],
                    selected_option={"key": chosen_key, "label": chosen_key},
                    operator_id="prop_tester",
                )

            sync_sqlite_to_json_export(s_path, j_path)
            data = json.loads(j_path.read_text(encoding="utf-8"))

            self.assertEqual(len(data["operator_decisions"]), len(decision_tuples))
            self.assertEqual(len(data["decisions"]), len(decision_tuples))

            # Verify Merkle links in exported JSON
            prev_hash = "0" * 64
            for r in data["operator_decisions"]:
                self.assertEqual(r["previous_entry_hash"], prev_hash)
                prev_hash = r["entry_digest"]


if __name__ == "__main__":
    unittest.main()
