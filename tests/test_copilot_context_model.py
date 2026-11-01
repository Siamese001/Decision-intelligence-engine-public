"""
tests/test_copilot_context_model.py

Unit tests for ConversationContextModel:
1. Decay math: entity mentioned once at min 1 ranks below entity mentioned 3 times in last 5 minutes.
2. Recall over time: risk raised at min 2 is among retrieval seeds at min 40 (regression pin against E7).
3. Conversational act detection: macro-F1 >= 0.8 against checked-in fixture tests/fixtures/conversational_acts.json.
4. Determinism: identical turns yield identical compute_hash and retrieval seeds.
5. Performance & Cost: add_turn p95 < 0.5ms and bounded memory over 2,000 turns.
6. Flow state components: topic segments, open questions, commitments, decisions, objections, speaker turns.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
    classify_conversational_act,
)


class TestCopilotContextModel(unittest.TestCase):
    def test_decay_math_recent_dominates_stale(self) -> None:
        """Acceptance Criteria 2: Entity mentioned once at min 1 ranks below entity mentioned 3 times in last 5 minutes."""
        model = ConversationContextModel(
            project_scope="project_lens",
            focus_seconds=15.0,
            decay_tau_seconds=300.0,
            focus_weight=0.6,
        )

        # Min 1 (60s): LegacyTool mentioned once
        model.add_turn("Amit", "We evaluated LegacyTool earlier today.", "00:01:00", turn_sec=60.0)

        # Min 35-39: ModernPlatform mentioned 3 times in last 5 minutes (eval at 2400s)
        model.add_turn("Jen", "ModernPlatform has low latency.", "00:35:00", turn_sec=2100.0)
        model.add_turn("David", "ModernPlatform meets our throughput goals.", "00:37:00", turn_sec=2220.0)
        model.add_turn("Jen", "ModernPlatform is ready for staging.", "00:39:00", turn_sec=2340.0)

        salient = model.salient_entities()
        self.assertGreaterEqual(len(salient), 2)
        top_entity, top_score = salient[0]
        self.assertEqual(top_entity, "ModernPlatform")

        modern_score = next(score for ent, score in salient if ent == "ModernPlatform")
        legacy_score = next(score for ent, score in salient if ent == "LegacyTool")
        self.assertGreater(modern_score, legacy_score)
        # Ratio should be very large (hundreds to thousands) due to exponential decay
        self.assertGreater(modern_score / legacy_score, 100.0)

    def test_recall_over_time_risk_recalled_at_min_40(self) -> None:
        """Acceptance Criteria 1: Risk raised at min 2 is among retrieval seeds at min 40 when topic returns."""
        model = ConversationContextModel(
            project_scope="project_lens",
            focus_seconds=15.0,
            decay_tau_seconds=300.0,
            salient_entities_top_k=8,
        )

        # Min 2 (120s): Risk raised regarding Oracle
        model.add_turn("Amit", "There is a severe risk of Oracle migration lock-in.", "00:02:00", turn_sec=120.0)
        self.assertEqual(model.current_act, "objection")

        # 38 minutes pass with other agenda items
        for sec in range(180, 2400, 120):
            model.add_turn("Team", f"Processing sprint work items at time {sec}.", "00:15:00", turn_sec=float(sec))

        # Under legacy window (last 4 turns), Oracle is completely lost:
        recent_texts = [t["text"] for t in model.focus_turns()]
        self.assertFalse(any("Oracle" in txt for txt in recent_texts))

        # Min 40 (2400s): Topic returns to database architecture
        model.add_turn("Jen", "Let us revisit our database options and review the Oracle risk.", "00:40:00", turn_sec=2400.0)

        seeds = model.retrieval_seeds()
        self.assertTrue(any("Oracle" in s for s in seeds), f"Oracle should be in seeds: {seeds}")

    def test_conversational_act_detection_macro_f1_above_threshold(self) -> None:
        """Acceptance Criteria 3: Labeled fixture set of conversational acts reaches >= 0.8 macro-F1."""
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "conversational_acts.json"
        self.assertTrue(fixture_path.is_file(), f"Fixture file not found: {fixture_path}")

        records = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(records), 30)

        per_class: dict[str, dict[str, int]] = {}
        for r in records:
            exp = r["expected_act"]
            pred = classify_conversational_act(r["text"], r.get("speaker"))
            if exp not in per_class:
                per_class[exp] = {"tp": 0, "fp": 0, "fn": 0}
            if pred not in per_class:
                per_class[pred] = {"tp": 0, "fp": 0, "fn": 0}
            if exp == pred:
                per_class[exp]["tp"] += 1
            else:
                per_class[exp]["fn"] += 1
                per_class[pred]["fp"] += 1

        f1_scores = []
        for _c, m in per_class.items():
            tp, fp, fn = m["tp"], m["fp"], m["fn"]
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
            f1_scores.append(f1)

        macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
        self.assertGreaterEqual(macro_f1, 0.80, f"Macro-F1 {macro_f1:.4f} is below 0.80 floor")

    def test_determinism_identical_turns_produce_identical_hash_and_seeds(self) -> None:
        """Acceptance Criteria 4: The same turns produce the exact same compute_hash and seeds."""
        turns = [
            ("Mandy", "Moving on to the next topic, let us discuss SOW deliverables.", "00:01:00", 60.0),
            ("Amit", "We agreed on allocating three senior engineers to Sprint Zero.", "00:02:00", 120.0),
            ("Jen", "Can you confirm if the delivery date is locked for June 1st?", "00:03:00", 180.0),
            ("David", "I will draft the technical design document by Friday afternoon.", "00:04:00", 240.0),
        ]

        m1 = ConversationContextModel(project_scope="project_lens")
        for spk, txt, ts, sec in turns:
            m1.add_turn(spk, txt, ts, turn_sec=sec)

        m2 = ConversationContextModel(project_scope="project_lens")
        for spk, txt, ts, sec in turns:
            m2.add_turn(spk, txt, ts, turn_sec=sec)

        self.assertEqual(m1.compute_hash(), m2.compute_hash())
        self.assertEqual(m1.retrieval_seeds(), m2.retrieval_seeds())
        self.assertEqual(m1.current_act, m2.current_act)

    def test_performance_cost_add_turn_sub_millisecond_and_memory_bounded(self) -> None:
        """Acceptance Criteria 5: add_turn p95 < 0.5 ms over a 2,000-turn 3-hour fixture."""
        model = ConversationContextModel(project_scope="project_lens")
        latencies = []

        speakers = ["Amit", "Jen", "Mandy", "David", "Sarah"]
        sample_utterances = [
            "We completed the API gateway validation on GCP yesterday.",
            "Can you confirm if the SLA is acceptable for customer onboarding?",
            "There is a severe risk of pipeline deadlock under high concurrency.",
            "We decided to proceed with PostgreSQL for persistent storage.",
            "I will own the migration script and verify automated rollbacks.",
            "Moving on to the next topic, let us examine the budget allocations.",
        ]

        for i in range(2000):
            spk = speakers[i % len(speakers)]
            txt = sample_utterances[i % len(sample_utterances)]
            ts = f"{i//3600:02d}:{(i%3600)//60:02d}:{i%60:02d}"
            sec = float(i * 5.4)  # 2000 * 5.4s = 10,800s (3 hours)

            t0 = time.perf_counter()
            model.add_turn(spk, txt, ts, turn_sec=sec)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(elapsed_ms)

        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]
        self.assertLess(p95, 0.50, f"add_turn p95 ({p95:.4f} ms) exceeded 0.5 ms budget")

        # Memory bound check: internal turn list exists, flow state remains bounded
        fs = model.flow_state
        self.assertLessEqual(len(fs.topic_segments), model.flow_items_max)
        self.assertLessEqual(len(fs.open_questions), model.flow_items_max)
        self.assertLessEqual(len(fs.commitments), model.flow_items_max)
        self.assertLessEqual(len(fs.decisions), model.flow_items_max)
        self.assertLessEqual(len(fs.objections_and_risks), model.flow_items_max)

    def test_flow_state_components(self) -> None:
        """Verifies topic segments, questions, commitments, decisions, and objections in flow state."""
        model = ConversationContextModel(project_scope="project_lens")

        model.add_turn("Mandy", "Moving on to the next topic, let us examine SOW boundaries.", "00:01:00", 60.0)
        self.assertEqual(model.current_act, "topic_shift")
        self.assertEqual(len(model.flow_state.topic_segments), 1)

        model.add_turn("Jen", "Can you verify if the data migration script has been tested?", "00:02:00", 120.0)
        self.assertEqual(model.current_act, "question_to_operator")
        self.assertEqual(len(model.flow_state.open_questions), 1)

        model.add_turn("Amit", "There is a severe risk of data corruption without checksums.", "00:03:00", 180.0)
        self.assertEqual(model.current_act, "objection")
        self.assertEqual(len(model.flow_state.objections_and_risks), 1)

        model.add_turn("Mandy", "We decided to enforce cryptographic checksums on every chunk.", "00:04:00", 240.0)
        self.assertEqual(model.current_act, "decision_point")
        self.assertEqual(len(model.flow_state.decisions), 1)

        model.add_turn("Amit", "I will draft the checksum verification module by tomorrow.", "00:05:00", 300.0)
        self.assertEqual(model.current_act, "commitment")
        self.assertEqual(len(model.flow_state.commitments), 1)

        summary = model.format_flow_summary()
        self.assertIn("Current Topic", summary)
        self.assertIn("Open Question", summary)
        self.assertIn("Recent Decision", summary)
        self.assertIn("Recent Commitment", summary)
        self.assertIn("Active Concern", summary)


if __name__ == "__main__":
    unittest.main()
