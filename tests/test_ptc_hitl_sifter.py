"""
tests/test_ptc_hitl_sifter.py

Unit tests for Programmatic Ambiguity Sifter & Queue Manager (PASQM) in Calibrated HITL Governance.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.hitl_sifter import (
    PTCAmbiguitySifter,
    PTCHITLCandidate,
    PTCHITLOption,
    PTCHITLQueueManager,
)
from dual_pipeline.ptc.sandbox import PTCSandbox


class TestPTCHITLSifter(unittest.TestCase):

    def setUp(self) -> None:
        self.sifter = PTCAmbiguitySifter(
            priors_map={"entity:tanner_stevens": 1.25, "entity:tanner_bell": 0.85}
        )

    def test_prior_calibration_boosts_confidence(self) -> None:
        opt_stevens = PTCHITLOption(
            option_id="opt_stevens",
            title="Tanner Stevens (VP of Engineering)",
            raw_confidence=0.70,
            pattern_key="entity:tanner_stevens",
        )
        opt_bell = PTCHITLOption(
            option_id="opt_bell",
            title="Tanner Bell (Consultant)",
            raw_confidence=0.60,
            pattern_key="entity:tanner_bell",
        )

        cal_stevens = self.sifter.compute_calibrated_confidence(opt_stevens)
        cal_bell = self.sifter.compute_calibrated_confidence(opt_bell)

        # 0.70 * 1.25 = 0.875
        self.assertAlmostEqual(cal_stevens, 0.875, places=3)
        # 0.60 * 0.85 = 0.51
        self.assertAlmostEqual(cal_bell, 0.51, places=3)

    def test_25_percent_margin_auto_resolution(self) -> None:
        cand_decisive = PTCHITLCandidate(
            candidate_id="cand_01",
            question="Disambiguate speaker for 'Tanner'",
            criticality="P1",
            options=[
                PTCHITLOption(
                    option_id="opt_stevens",
                    title="Tanner Stevens",
                    raw_confidence=0.70,
                    pattern_key="entity:tanner_stevens",
                ),
                PTCHITLOption(
                    option_id="opt_bell",
                    title="Tanner Bell",
                    raw_confidence=0.60,
                    pattern_key="entity:tanner_bell",
                ),
            ],
        )

        result = self.sifter.sift([cand_decisive], margin_threshold=0.25)

        # Delta = 0.875 - 0.51 = 0.365 >= 0.25 -> Auto resolved!
        self.assertEqual(result.auto_resolved_count, 1)
        self.assertEqual(result.ambiguous_count, 0)
        self.assertEqual(result.auto_resolved[0]["winner_option_id"], "opt_stevens")
        self.assertGreaterEqual(result.auto_resolved[0]["confidence_margin_delta"], 0.25)
        self.assertTrue(result.sift_digest.startswith("sha256:"))

    def test_ambiguity_queue_sorting_by_criticality(self) -> None:
        cand_p2 = PTCHITLCandidate(
            candidate_id="cand_p2",
            question="P2 Ambiguity",
            criticality="P2",
            options=[
                PTCHITLOption(option_id="o1", title="A", raw_confidence=0.55),
                PTCHITLOption(option_id="o2", title="B", raw_confidence=0.50),
            ],
        )
        cand_p0 = PTCHITLCandidate(
            candidate_id="cand_p0",
            question="P0 Critical Ambiguity",
            criticality="P0",
            options=[
                PTCHITLOption(option_id="o1", title="A", raw_confidence=0.60),
                PTCHITLOption(option_id="o2", title="B", raw_confidence=0.55),
            ],
        )
        cand_p1 = PTCHITLCandidate(
            candidate_id="cand_p1",
            question="P1 Architectural Ambiguity",
            criticality="P1",
            options=[
                PTCHITLOption(option_id="o1", title="A", raw_confidence=0.58),
                PTCHITLOption(option_id="o2", title="B", raw_confidence=0.52),
            ],
        )

        sifter = PTCAmbiguitySifter()
        result = sifter.sift([cand_p2, cand_p0, cand_p1], margin_threshold=0.25)

        self.assertEqual(result.ambiguous_count, 3)
        # Should be ordered P0 -> P1 -> P2
        self.assertEqual(result.ambiguous_queue[0]["candidate_id"], "cand_p0")
        self.assertEqual(result.ambiguous_queue[1]["candidate_id"], "cand_p1")
        self.assertEqual(result.ambiguous_queue[2]["candidate_id"], "cand_p2")

    def test_atomic_single_item_popping(self) -> None:
        qm = PTCHITLQueueManager(margin_threshold=0.25)
        c1 = PTCHITLCandidate(
            candidate_id="cand_01",
            question="Resolve ambiguity 1",
            criticality="P0",
            options=[
                PTCHITLOption(option_id="o1", title="Primary", raw_confidence=0.60),
                PTCHITLOption(option_id="o2", title="Secondary", raw_confidence=0.55),
            ],
        )
        c2 = PTCHITLCandidate(
            candidate_id="cand_02",
            question="Resolve ambiguity 2",
            criticality="P1",
            options=[
                PTCHITLOption(option_id="o3", title="Alt 1", raw_confidence=0.58),
                PTCHITLOption(option_id="o4", title="Alt 2", raw_confidence=0.50),
            ],
        )

        qm.load_candidates([c2, c1])
        self.assertEqual(qm.get_remaining_ambiguous_count(), 2)

        # Atomic Pop 1: should pop cand_01 (higher criticality P0)
        pop1 = qm.pop_next_atomic()
        self.assertIsNotNone(pop1)
        self.assertEqual(pop1["candidate_id"], "cand_01")
        self.assertTrue(pop1["options"][0]["title"].startswith("(Recommended)"))
        self.assertEqual(qm.get_remaining_ambiguous_count(), 1)

        # Atomic Pop 2: should pop cand_02 (P1)
        pop2 = qm.pop_next_atomic()
        self.assertIsNotNone(pop2)
        self.assertEqual(pop2["candidate_id"], "cand_02")
        self.assertEqual(qm.get_remaining_ambiguous_count(), 0)

        # Atomic Pop 3: empty queue
        pop3 = qm.pop_next_atomic()
        self.assertIsNone(pop3)

    def test_dynamic_queue_recalibration_promotes_downstream_candidates(self) -> None:
        qm = PTCHITLQueueManager(margin_threshold=0.25)

        # Candidate 1: Tanner in transcript 1 (ambiguous: raw delta = 0.05 < 0.25)
        c1 = PTCHITLCandidate(
            candidate_id="cand_01",
            question="Disambiguate Tanner in Section 1",
            criticality="P1",
            options=[
                PTCHITLOption(
                    option_id="opt_stevens_1",
                    title="Tanner Stevens",
                    raw_confidence=0.55,
                    pattern_key="entity:tanner_stevens",
                ),
                PTCHITLOption(
                    option_id="opt_bell_1",
                    title="Tanner Bell",
                    raw_confidence=0.50,
                    pattern_key="entity:tanner_bell",
                ),
            ],
        )

        # Candidate 2: Tanner in transcript 2 (ambiguous: raw delta = 0.15 < 0.25)
        c2 = PTCHITLCandidate(
            candidate_id="cand_02",
            question="Disambiguate Tanner in Section 2",
            criticality="P1",
            options=[
                PTCHITLOption(
                    option_id="opt_stevens_2",
                    title="Tanner Stevens",
                    raw_confidence=0.65,
                    pattern_key="entity:tanner_stevens",
                ),
                PTCHITLOption(
                    option_id="opt_bell_2",
                    title="Tanner Bell",
                    raw_confidence=0.50,
                    pattern_key="entity:tanner_bell",
                ),
            ],
        )

        qm.load_candidates([c1, c2])
        self.assertEqual(qm.get_remaining_ambiguous_count(), 2)

        # Operator resolves Candidate 1 affirming Tanner Stevens with a 1.25x boost
        res = qm.record_operator_resolution(
            candidate_id="cand_01",
            selected_option_id="opt_stevens_1",
            boost_factor=1.25,
            penalty_factor=0.85,
        )

        # Candidate 2: new delta = (0.65 * 1.25) - (0.50 * 0.85) = 0.8125 - 0.4250 = 0.3875 >= 0.25!
        # It should have been automatically promoted during dynamic re-sifting!
        self.assertEqual(res["promoted_by_recalibration"], 1)
        self.assertEqual(qm.get_remaining_ambiguous_count(), 0)
        self.assertEqual(len(qm.get_auto_resolved()), 1)
        self.assertEqual(qm.get_auto_resolved()[0]["candidate_id"], "cand_02")
        self.assertEqual(qm.get_auto_resolved()[0]["winner_option_id"], "opt_stevens_2")

    def test_sandbox_executes_hitl_sifter(self) -> None:
        class SifterProxy:
            def __init__(self, sifter):
                self.sifter = sifter

            def sift_candidates(self, candidates_data):
                cands = []
                for c in candidates_data:
                    opts = [PTCHITLOption(**o) for o in c["options"]]
                    cands.append(PTCHITLCandidate(
                        candidate_id=c["candidate_id"],
                        question=c["question"],
                        criticality=c.get("criticality", "P1"),
                        options=opts,
                    ))
                return self.sifter.sift(cands).to_dict()

        script = """
def run(proxy):
    cands = [
        {
            "candidate_id": "c_auto",
            "question": "Auto question",
            "options": [
                {"option_id": "o1", "title": "Top", "raw_confidence": 0.90},
                {"option_id": "o2", "title": "Low", "raw_confidence": 0.50}
            ]
        }
    ]
    res = proxy.sift_candidates(cands)
    return {
        "auto_count": res["auto_resolved_count"],
        "amb_count": res["ambiguous_count"],
        "winner": res["auto_resolved"][0]["winner_option_id"]
    }
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_hitl_sandbox",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"proxy": SifterProxy(self.sifter)},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["auto_count"], 1)
        self.assertEqual(summary.data["amb_count"], 0)
        self.assertEqual(summary.data["winner"], "o1")


if __name__ == "__main__":
    unittest.main()
