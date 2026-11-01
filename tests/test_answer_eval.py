"""Unit tests for Answer Evaluation Harness (Wave 7)."""
from __future__ import annotations

import sqlite3
import unittest

from dual_pipeline.evals.answer_eval_core import (
    compute_citation_durability,
    compute_context_recall,
    compute_eval_receipt_digest,
    compute_groundedness,
    compute_latency_stats,
    compute_operator_usefulness,
    compute_staleness_and_conflict_miss,
    replay_candidate_parameters,
    validate_eval_receipt,
)


class TestAnswerEvalUnit(unittest.TestCase):
    def test_compute_groundedness(self) -> None:
        rec_grounded = {
            "response_text": "The SCADA Gateway is constrained [ast-001]. PI Historian buffer is full ast-002.",
            "cited_assertion_ids": ["ast-001", "ast-002"],
        }
        score = compute_groundedness(rec_grounded)
        self.assertAlmostEqual(score, 1.0)

        rec_ungrounded = {
            "response_text": "We should schedule a meeting tomorrow. Everything seems fine.",
            "cited_assertion_ids": [],
        }
        score_un = compute_groundedness(rec_ungrounded)
        self.assertAlmostEqual(score_un, 0.0)

        rec_partial = {
            "response_text": "Project Nexus has risks [ast-001]. No other updates at this time.",
            "cited_assertion_ids": ["ast-001"],
        }
        score_part = compute_groundedness(rec_partial)
        self.assertAlmostEqual(score_part, 0.5)

    def test_citation_durability(self) -> None:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, canonical_status TEXT, retrieval_eligible INTEGER)")
        con.execute("INSERT INTO assertion VALUES ('ast-001', 'affirmed', 1)")
        con.execute("INSERT INTO assertion VALUES ('ast-002', 'revoked', 0)")
        con.commit()

        dur = compute_citation_durability(["ast-001", "ast-002"], con)
        self.assertAlmostEqual(dur, 0.5)

        dur_all_active = compute_citation_durability(["ast-001"], con)
        self.assertAlmostEqual(dur_all_active, 1.0)

        dur_empty = compute_citation_durability([], con)
        self.assertAlmostEqual(dur_empty, 0.0)

    def test_staleness_and_conflict_miss(self) -> None:
        con = sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            source_date_normalized TEXT,
            canonical_status TEXT
        )""")
        con.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_relationship TEXT)")

        con.execute("INSERT INTO assertion VALUES ('ast-old', 'node1', 'OWNS', '2026-01-01', 'affirmed')")
        con.execute("INSERT INTO assertion VALUES ('ast-new', 'node1', 'OWNS', '2026-06-01', 'affirmed')")
        con.execute("INSERT INTO assertion_evidence VALUES ('ast-old', 'CONTRADICTS')")
        con.commit()

        rec = {
            "cited_assertion_ids": ["ast-old"],
            "response_text": "Node1 owns the project without issue.",
        }
        stale, conf_miss = compute_staleness_and_conflict_miss(rec, con)
        self.assertAlmostEqual(stale, 1.0)
        self.assertAlmostEqual(conf_miss, 1.0)

        rec_aware = {
            "cited_assertion_ids": ["ast-old"],
            "response_text": "Node1 owns the project [CONTRADICTION DETECTED across dates].",
        }
        stale2, conf_miss2 = compute_staleness_and_conflict_miss(rec_aware, con)
        self.assertAlmostEqual(stale2, 1.0)
        self.assertAlmostEqual(conf_miss2, 0.0)

    def test_context_recall(self) -> None:
        rec = {
            "context_snapshot": [
                {"speaker": "Jen", "text": "What about Project Nexus and PI Historian?"}
            ],
            "retrieval_seeds": ["Project Nexus"],
            "response_text": "We found PI Historian is constrained.",
        }
        recall = compute_context_recall(rec)
        self.assertAlmostEqual(recall, 1.0)

        rec_miss = {
            "context_snapshot": [
                {"speaker": "Jen", "text": "What about SCADA Gateway and Substation?"}
            ],
            "retrieval_seeds": [],
            "response_text": "No information available.",
        }
        recall_miss = compute_context_recall(rec_miss)
        self.assertAlmostEqual(recall_miss, 0.0)

    def test_operator_usefulness_beta_clamping(self) -> None:
        # All useful: (1 + 10) / (2 + 10) = 11/12 = 0.9167
        fb_all_useful = [{"rating": "useful"} for _ in range(10)]
        stats = compute_operator_usefulness(fb_all_useful)
        self.assertAlmostEqual(stats.beta_posterior_mean, 0.9167, places=3)

        # Extreme positive clamped to 0.95
        fb_extreme = [{"rating": "useful"} for _ in range(100)]
        stats_ext = compute_operator_usefulness(fb_extreme)
        self.assertEqual(stats_ext.beta_posterior_mean, 0.95)

        # Extreme negative clamped to 0.05
        fb_wrong = [{"rating": "wrong"} for _ in range(100)]
        stats_wrong = compute_operator_usefulness(fb_wrong)
        self.assertEqual(stats_wrong.beta_posterior_mean, 0.05)

    def test_latency_stats(self) -> None:
        records = [
            {"latency_ms": 10.0, "rigor": "FAST"},
            {"latency_ms": 20.0, "rigor": "FAST"},
            {"latency_ms": 30.0, "rigor": "VERIFIED"},
            {"latency_ms": 100.0, "rigor": "VERIFIED"},
        ]
        stats = compute_latency_stats(records)
        self.assertEqual(stats.p50_ms, 20.0)
        self.assertEqual(stats.p95_ms, 100.0)
        self.assertIn("FAST", stats.by_rigor_tier)
        self.assertIn("VERIFIED", stats.by_rigor_tier)

    def test_replay_mode_sanity_pin(self) -> None:
        records = [
            {
                "pipeline": "copilot",
                "context_snapshot": [{"speaker": "Jen", "text": "Nexus update"}],
                "cited_assertion_ids": ["ast-001", "ast-002"],
                "response_text": "Nexus status [ast-001] [ast-002].",
                "latency_ms": 15.0,
            }
        ]
        # Unchanged parameters MUST reproduce citations exactly (match rate = 1.0)
        candidate_params = {}
        replay = replay_candidate_parameters(records, candidate_params)
        self.assertEqual(replay.reproduced_citation_exact_match_rate, 1.0)
        self.assertEqual(replay.replayed_count, 1)
        self.assertAlmostEqual(replay.groundedness_delta, 0.0)

    def test_receipt_digest_determinism_and_validation(self) -> None:
        receipt = {
            "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
            "schema_version": "1.0.0",
            "eval_id": "eval-ans-a1b2c3d4e5f6",
            "timestamp": "2026-10-05T12:00:00Z",
            "pipeline": "both",
            "ledger_range": {
                "start_answer_id": "ans-001",
                "end_answer_id": "ans-002",
                "record_count": 2,
                "chain_valid": True,
            },
            "graph_sha256": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "generation": 1,
            "parameter_snapshot_digest": None,
            "metrics": {
                "groundedness": 0.85,
                "citation_durability": 1.0,
                "staleness_rate": 0.0,
                "conflict_miss_rate": 0.0,
                "context_recall": 0.90,
                "operator_usefulness": {
                    "useful_count": 5,
                    "not_useful_count": 1,
                    "wrong_count": 0,
                    "total_feedback": 6,
                    "beta_posterior_mean": 0.75,
                },
                "post_meeting_uptake": 0.5,
                "latency": {
                    "p50_ms": 25.0,
                    "p95_ms": 50.0,
                    "by_rigor_tier": {
                        "FAST": {"p50_ms": 10.0, "p95_ms": 15.0, "count": 1},
                        "VERIFIED": {"p50_ms": 40.0, "p95_ms": 50.0, "count": 1},
                    },
                },
                "fail_closed_rate": 0.0,
            },
            "replay_comparison": None,
            "llm_judge": None,
        }
        d1 = compute_eval_receipt_digest(receipt)
        d2 = compute_eval_receipt_digest(receipt)
        self.assertEqual(d1, d2)

        receipt["receipt_digest"] = d1
        validate_eval_receipt(receipt)


if __name__ == "__main__":
    unittest.main()
