"""
tests/test_hitl_pattern_key_and_telemetry.py

Unit, property, and integration tests for Wave 2:
1. Canonical pattern key extraction and normalization (timestamps, quotes, turn counts, confidence suffixes).
2. Key matching and historical prior linkage across varying meeting/turn contexts.
3. SSOT confidence tier classification (HIGH >= 0.85, MEDIUM >= 0.50, LOW < 0.50) and label percentage refresh.
4. Enriched telemetry persistence (outcome_type, margin_at_decision, confidence_interval_at_decision) in record_operator_answer.
5. Hypothesis property tests for robust normalization and invariant preservation.
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

from dual_pipeline.hitl_governance_core.bayesian import (
    _match_keys,
    canonicalize_pattern_key,
    compute_bayesian_calibrated_score,
    update_label_confidence,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    resolve_ledger_paths,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from storage.connection import create_sqlite_conn


class PatternKeyNormalizationTests(unittest.TestCase):
    """Verifies canonical pattern key extraction and noise elimination."""

    def test_strip_presentation_prefixes(self) -> None:
        self.assertEqual(
            canonicalize_pattern_key("(Recommended) Approve ingestion"),
            "approve ingestion",
        )
        self.assertEqual(
            canonicalize_pattern_key("[Recommended] — Assign turn to Alice"),
            "assign turn to alice",
        )
        self.assertEqual(
            canonicalize_pattern_key("RECOMMENDED: Approve release"),
            "approve release",
        )

    def test_strip_confidence_indicators(self) -> None:
        self.assertEqual(
            canonicalize_pattern_key("Approve candidate (Confidence: HIGH (95%))"),
            "approve candidate",
        )
        self.assertEqual(
            canonicalize_pattern_key("Assign speaker — Confidence: MEDIUM (60%)"),
            "assign speaker",
        )
        self.assertEqual(
            canonicalize_pattern_key("Keep entity [Confidence: 80%]"),
            "keep entity",
        )

    def test_strip_timestamps(self) -> None:
        self.assertEqual(
            canonicalize_pattern_key("Assign turn [00:05:10] to Alice"),
            "assign turn to alice",
        )
        self.assertEqual(
            canonicalize_pattern_key("Assign turn 12:45 to Bob Smith"),
            "assign turn to bob smith",
        )
        self.assertEqual(
            canonicalize_pattern_key("Turn at [01:23:45] - assign to Carol"),
            "turn at assign to carol",
        )

    def test_strip_turn_counts(self) -> None:
        self.assertEqual(
            canonicalize_pattern_key("Assign cluster (14 turns) to Alice"),
            "assign cluster to alice",
        )
        self.assertEqual(
            canonicalize_pattern_key("Assign cluster (1 turn) to Bob"),
            "assign cluster to bob",
        )

    def test_strip_quotes_and_speaker_prefixes(self) -> None:
        self.assertEqual(
            canonicalize_pattern_key('Speaker 1: "We should migrate to Cloud SQL"'),
            "we should migrate to cloud sql",
        )
        self.assertEqual(
            canonicalize_pattern_key("speaker_02: 'Agree on timeline'"),
            "agree on timeline",
        )


class KeyMatchingAndPriorTests(unittest.TestCase):
    """Verifies that canonical pattern keys match historical priors across meetings."""

    def test_speaker_attribution_with_varying_timestamps(self) -> None:
        key_meeting_1 = "Assign turn [00:05:10] to Alice Smith"
        key_meeting_2 = "(Recommended) Assign turn [00:18:45] to Alice Smith (Confidence: HIGH (92%))"
        self.assertTrue(_match_keys(key_meeting_1, key_meeting_2))

    def test_title_clarification_matching(self) -> None:
        prior_choice = "Approve executive title VP Engineering"
        new_prompt = "Approve executive title VP of Engineering"
        self.assertTrue(_match_keys(prior_choice, new_prompt))

    def test_ingestion_approval_matching(self) -> None:
        prior_approval = "Approve publication and activation for release 2026-10-14"
        new_approval = "(Recommended) Approve publication and activation for release 2026-10-15 (Confidence: HIGH (95%))"
        self.assertTrue(_match_keys(prior_approval, new_approval))

    def test_disqualifying_action_modifiers_prevent_collision(self) -> None:
        # Conflicting actions must NEVER match despite sharing entity tokens
        self.assertFalse(_match_keys("Delete node 123", "Approve node 123"))
        self.assertFalse(_match_keys("Reject title VP Engineering", "Approve title VP Engineering"))
        self.assertFalse(_match_keys("Remove speaker Alice", "Add speaker Alice"))

    def test_cross_entity_disjoint_names_prevent_collision(self) -> None:
        # Distinct people sharing same action verbs must NEVER match
        self.assertFalse(_match_keys("Assign turn to Alice Smith", "Assign turn to Bob Jones"))
        self.assertFalse(_match_keys("Assign to Alice Smith", "Assign to Bob Jones"))
        self.assertFalse(_match_keys("assign_alice", "Assign to Bob Jones"))
        self.assertFalse(_match_keys("assign_bob", "Assign to Alice Smith"))

    def test_prior_accumulation_with_varying_presentation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            s_path, j_path = resolve_ledger_paths(repo_root=root)
            human_origin = {"source": "USER_INPUT", "text": "Approve Alice"}

            # Record prior decision with timestamp [00:02:15]
            record_operator_answer(
                decision_id="dec_001",
                question="Assign turn [00:02:15] to speaker?",
                options=["Assign to Alice Smith", "Assign to Bob Jones"],
                selected_option="Assign to Alice Smith",
                operator_origin=human_origin,
                category="speaker_attribution",
                sqlite_path=s_path,
                json_path=j_path,
            )

            # Now evaluate a new option with different timestamp and recommended prefix
            store = HITLDecisionStore(ledger_path=j_path, sqlite_path=s_path)
            new_options = [
                {
                    "key": "assign_alice",
                    "label": "(Recommended) Assign turn [00:45:10] to Alice Smith (Confidence: HIGH (90%))",
                    "confidence": 0.90,
                },
                {
                    "key": "assign_bob",
                    "label": "Assign turn [00:45:10] to Bob Jones (Confidence: LOW (10%))",
                    "confidence": 0.10,
                },
            ]

            calibrated = store.calibrate_options(new_options, category="speaker_attribution")
            alice_opt = calibrated[0]
            self.assertGreaterEqual(alice_opt["confidence_sample_count"], 1)
            self.assertEqual(alice_opt["prior_affirmations"], 1)
            self.assertEqual(alice_opt["prior_rejections"], 0)


class ConfidenceTierAndLabelRefreshTests(unittest.TestCase):
    """Verifies SSOT tier classification and dynamic label updating."""

    def test_ssot_tier_boundaries(self) -> None:
        high = compute_bayesian_calibrated_score(0.85, 0.0, 0.0)
        self.assertEqual(high.tier, "HIGH")

        just_under_high = compute_bayesian_calibrated_score(0.8499, 0.0, 0.0)
        self.assertEqual(just_under_high.tier, "MEDIUM")

        medium = compute_bayesian_calibrated_score(0.50, 0.0, 0.0)
        self.assertEqual(medium.tier, "MEDIUM")

        low = compute_bayesian_calibrated_score(0.4999, 0.0, 0.0)
        self.assertEqual(low.tier, "LOW")

    def test_update_label_confidence_formats(self) -> None:
        # Standard parens format
        lbl1 = update_label_confidence("Option A (Confidence: HIGH (90%))", "MEDIUM", 0.65)
        self.assertEqual(lbl1, "Option A (Confidence: MEDIUM (65%))")

        # Dash format
        lbl2 = update_label_confidence("Option B — Confidence: LOW (30%)", "HIGH", 0.91)
        self.assertEqual(lbl2, "Option B — Confidence: HIGH (91%)")

        # Bracket format
        lbl3 = update_label_confidence("Option C [Confidence: HIGH (95%)]", "LOW", 0.42)
        self.assertEqual(lbl3, "Option C [Confidence: LOW (42%)]")

        # Simple percentage parens
        lbl4 = update_label_confidence("Choice D (60%)", "HIGH", 0.88)
        self.assertEqual(lbl4, "Choice D (88%)")

    def test_calibrate_options_updates_label_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            s_path, j_path = resolve_ledger_paths(repo_root=root)
            human_origin = {"source": "USER_INPUT", "text": "Reject recommended option"}

            # Record an override where Alice was rejected
            record_operator_answer(
                decision_id="dec_override_01",
                question="Assign turn to speaker?",
                options=[
                    {"key": "alice", "label": "Assign to Alice Smith", "recommended": True},
                    {"key": "bob", "label": "Assign to Bob Jones"},
                ],
                selected_option={"key": "bob", "label": "Assign to Bob Jones"},
                operator_origin=human_origin,
                category="speaker_attribution",
                sqlite_path=s_path,
                json_path=j_path,
            )

            store = HITLDecisionStore(ledger_path=j_path, sqlite_path=s_path)
            options_to_calibrate = [
                {
                    "key": "alice",
                    "label": "Assign to Alice Smith (Confidence: HIGH (90%))",
                    "confidence": 0.90,
                }
            ]

            calibrated = store.calibrate_options(options_to_calibrate, category="speaker_attribution")
            calibrated_label = calibrated[0]["label"]

            # Confirm label text was refreshed and no longer retains pre-calibration 90%
            self.assertNotIn("(90%)", calibrated_label)
            self.assertIn("Confidence:", calibrated_label)


class RecordOperatorAnswerTelemetryTests(unittest.TestCase):
    """Verifies that record_operator_answer computes and persists enriched telemetry."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.s_path, self.j_path = resolve_ledger_paths(repo_root=self.root)
        self.human_origin = {"source": "USER_INPUT", "text": "Confirmed"}

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_persists_enriched_telemetry_affirmed(self) -> None:
        options = [
            {"key": "opt_a", "label": "Option A (Confidence: HIGH (90%))", "recommended": True, "confidence": 0.90},
            {"key": "opt_b", "label": "Option B (Confidence: LOW (10%))", "confidence": 0.10},
        ]
        res = record_operator_answer(
            decision_id="dec_telemetry_affirmed",
            question="Choose preferred action?",
            options=options,
            selected_option=options[0],
            operator_origin=self.human_origin,
            category="governance_action",
            sqlite_path=self.s_path,
            json_path=self.j_path,
        )

        self.assertEqual(res.outcome_type, "AFFIRMED")
        self.assertAlmostEqual(res.margin_at_decision, 0.80, places=2)

        # 1. Independent SQLite verification
        conn = create_sqlite_conn(self.s_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM hitl_decisions WHERE entry_id = ?", (res.entry_id,))
        row = cur.fetchone()
        conn.close()
        self.assertIsNotNone(row)
        meta = json.loads(row[0])
        self.assertEqual(meta.get("outcome_type"), "AFFIRMED")
        self.assertAlmostEqual(meta.get("margin_at_decision"), 0.80, places=2)
        self.assertIn("confidence_interval_at_decision", meta)

        # 2. Independent JSON export verification
        json_data = json.loads(self.j_path.read_text(encoding="utf-8"))
        op_decisions = json_data.get("operator_decisions", [])
        self.assertTrue(len(op_decisions) >= 1)
        last_rec = op_decisions[-1]
        self.assertEqual(last_rec.get("outcome_type"), "AFFIRMED")
        self.assertAlmostEqual(last_rec.get("margin_at_decision"), 0.80, places=2)

    def test_persists_enriched_telemetry_overridden(self) -> None:
        options = [
            {"key": "opt_a", "label": "Option A (Confidence: HIGH (85%))", "recommended": True, "confidence": 0.85},
            {"key": "opt_b", "label": "Option B (Confidence: MEDIUM (55%))", "confidence": 0.55},
        ]
        res = record_operator_answer(
            decision_id="dec_telemetry_overridden",
            question="Choose preferred action?",
            options=options,
            selected_option=options[1],  # chose non-recommended
            operator_origin=self.human_origin,
            category="governance_action",
            sqlite_path=self.s_path,
            json_path=self.j_path,
        )

        self.assertEqual(res.outcome_type, "OVERRIDDEN")
        self.assertAlmostEqual(res.margin_at_decision, 0.30, places=2)

        conn = create_sqlite_conn(self.s_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM hitl_decisions WHERE entry_id = ?", (res.entry_id,))
        row = cur.fetchone()
        conn.close()
        meta = json.loads(row[0])
        self.assertEqual(meta.get("outcome_type"), "OVERRIDDEN")
        self.assertAlmostEqual(meta.get("margin_at_decision"), 0.30, places=2)

    def test_reconcile_selected_option_preserves_numeric_confidence(self) -> None:
        options = [
            {"key": "action_publish", "label": "Publish Release", "confidence": 0.95, "recommended": True},
            {"key": "action_hold", "label": "Hold Release", "confidence": 0.05},
        ]
        # Caller passes raw key string instead of dict
        res = record_operator_answer(
            decision_id="dec_raw_key_string",
            question="Publish release?",
            options=options,
            selected_option="action_publish",
            operator_origin=self.human_origin,
            category="release_lifecycle",
            sqlite_path=self.s_path,
            json_path=self.j_path,
        )

        conn = create_sqlite_conn(self.s_path, readonly=True)
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM hitl_decisions WHERE entry_id = ?", (res.entry_id,))
        meta = json.loads(cur.fetchone()[0])
        conn.close()

        # Reconciled option preserves upper bound > 0.80 derived from 0.95 confidence
        ci = meta.get("confidence_interval_at_decision")
        self.assertIsNotNone(ci)
        self.assertGreater(ci[1], 0.80)


class PatternKeyAndTelemetryPropertyTests(unittest.TestCase):
    """Hypothesis property-based tests for key normalization and telemetry invariants."""

    @settings(max_examples=50, deadline=None)
    @given(
        minute=st.integers(min_value=0, max_value=59),
        second=st.integers(min_value=0, max_value=59),
        prefix=st.sampled_from(["(Recommended) ", "[Recommended] - ", "RECOMMENDED: ", ""]),
        quote=st.sampled_from(['"', "'", "`", ""]),
        conf_pct=st.integers(min_value=10, max_value=99),
    )
    def test_property_pattern_key_timestamp_and_prefix_invariance(
        self, minute: int, second: int, prefix: str, quote: str, conf_pct: int
    ) -> None:
        ts = f"[{minute:02d}:{second:02d}]"
        raw_key = f"{prefix}Assign turn {ts} to {quote}Alice Smith{quote} (Confidence: HIGH ({conf_pct}%))"
        canonical = canonicalize_pattern_key(raw_key)

        self.assertEqual(canonical, "assign turn to alice smith")
        self.assertTrue(_match_keys("Assign turn to Alice Smith", raw_key))

    @settings(max_examples=30, deadline=None)
    @given(
        score1=st.floats(min_value=0.01, max_value=0.99),
        score2=st.floats(min_value=0.01, max_value=0.99),
    )
    def test_property_enriched_margin_always_finite_and_bounded(
        self, score1: float, score2: float
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            s_path, j_path = resolve_ledger_paths(repo_root=root)
            human_origin = {"source": "USER_INPUT", "text": "Approve"}

            options = [
                {"key": "opt_1", "label": "Option 1", "confidence": score1, "recommended": True},
                {"key": "opt_2", "label": "Option 2", "confidence": score2},
            ]
            res = record_operator_answer(
                decision_id=f"dec_prop_{abs(hash((score1, score2)))}",
                question="Property test question?",
                options=options,
                selected_option="opt_1",
                operator_origin=human_origin,
                category="property_test",
                sqlite_path=s_path,
                json_path=j_path,
            )

            self.assertIn(res.outcome_type, {"AFFIRMED", "OVERRIDDEN"})
            self.assertGreaterEqual(res.margin_at_decision, 0.0)
            self.assertLessEqual(res.margin_at_decision, 1.0)


if __name__ == "__main__":
    unittest.main()
