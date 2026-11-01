"""
tests/test_ptc_phase3_e2e.py

End-to-end integration test suite for Programmatic Tool Calling (PTC) Phase 3:
- Pipeline A Stage 03 Ambiguity Locks -> Calibrated HITL Sifter (PASQM)
- Atomic single-item popping and dynamic Bayesian prior recalibration
- Pipeline D Copilot Context Fusion Streaming (PCFS) with Law 3 enforcement
- AST-governed sandboxed execution and TokenBudget disk spooling
- Token optimization telemetry recording (> 80% token reduction)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.decision.context_fusion import CopilotFusionEngine
from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.entity_resolution import (
    PTCEntityResolutionPlan,
    PTCEntityResolver,
)
from dual_pipeline.ptc.hitl_sifter import (
    PTCAmbiguitySifter,
    PTCHITLCandidate,
    PTCHITLOption,
    PTCHITLQueueManager,
)
from dual_pipeline.ptc.sandbox import PTCSandbox
from dual_pipeline.ptc.spooler import PTCOutputSpooler
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent


class TestPTCPhase3EndToEnd(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)
        self.spool_dir = self.runtime_root / "spool"
        self.spooler = PTCOutputSpooler(spool_dir=self.spool_dir)
        self.sandbox = PTCSandbox(spooler=self.spooler)
        self.token_agent = TokenOptimizationAgent()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_ptc_stage03_ambiguity_to_pasqm_sifting_in_sandbox(self) -> None:
        resolver = PTCEntityResolver()

        class PipelineAProxy:
            def __init__(self, res: PTCEntityResolver):
                self.res = res

            def resolve_and_sift(self, raw_triples, meeting_date, priors):
                plan = PTCEntityResolutionPlan(
                    triples=raw_triples,
                    meeting_date=meeting_date,
                )
                resolved = self.res.resolve(plan)

                # Convert ambiguity locks to PTCHITLCandidates
                candidates = []
                for lock in resolved.ambiguity_locks:
                    c_id = f"lock_{lock['subject']}_{lock['predicate']}"
                    opts = [
                        PTCHITLOption(
                            option_id=f"{lock['subject']}_opt1",
                            title=f"Affirm {lock['subject']}",
                            raw_confidence=lock["confidence"],
                            pattern_key=f"entity:{lock['subject']}",
                        ),
                        PTCHITLOption(
                            option_id=f"{lock['subject']}_opt2",
                            title=f"Alternative for {lock['subject']}",
                            raw_confidence=0.50,
                            pattern_key=f"entity:{lock['subject']}_alt",
                        ),
                    ]
                    candidates.append(PTCHITLCandidate(
                        candidate_id=c_id,
                        question=f"Disambiguate {lock['subject']}",
                        criticality="P1",
                        options=opts,
                    ))

                sifter = PTCAmbiguitySifter(priors_map=priors)
                sift_res = sifter.sift(candidates, margin_threshold=0.25)
                return {
                    "grounded_count": resolved.promoted_count,
                    "ambiguity_count": resolved.ambiguity_count,
                    "sift_result": sift_res.to_dict(),
                }

        script = """
def run(proxy):
    triples = [
        # Ambiguous entity with strong prior (1.30x boost) -> should auto-resolve (0.75 * 1.30 = 0.975 vs 0.50 -> delta = 0.475 >= 0.25)
        {
            "subject": "person:tanner",
            "predicate": "owns",
            "object": "system:governance_engine",
            "kind": "strategic",
            "confidence": 0.75
        },
        # Ambiguous entity with neutral prior (1.0x) -> should remain ambiguous (0.70 vs 0.50 -> delta = 0.20 < 0.25)
        {
            "subject": "system:kafka_lag",
            "predicate": "impacts",
            "object": "system:event_stream",
            "kind": "risk",
            "confidence": 0.70
        }
    ]
    priors = {"entity:person:tanner": 1.30}
    return proxy.resolve_and_sift(triples, "09/13/2026", priors)
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_pasqm_sifting",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"proxy": PipelineAProxy(resolver)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)

        data = result.data
        self.assertEqual(data["grounded_count"], 2)
        self.assertEqual(data["ambiguity_count"], 2)

        sift = data["sift_result"]
        self.assertEqual(sift["auto_resolved_count"], 1)
        self.assertEqual(sift["ambiguous_count"], 1)

        auto = sift["auto_resolved"][0]
        self.assertEqual(auto["winner_option_id"], "person:tanner_opt1")
        self.assertGreaterEqual(auto["confidence_margin_delta"], 0.25)

        amb = sift["ambiguous_queue"][0]
        self.assertLess(amb["confidence_margin_delta"], 0.25)
        self.assertTrue(amb["options"][0]["title"].startswith("(Recommended)"))

        # Telemetry: Compare baseline multi-turn HITL dialogue (~14KB) vs PTC in-process sifting (<1.5KB)
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_pasqm_sifting",
            baseline_bytes=14_336,
            spooled_summary_bytes=result.payload_bytes,
            execution_time_ms=result.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.80)

    def test_e2e_ptc_queue_manager_atomic_popping_and_dynamic_calibration(self) -> None:
        qm = PTCHITLQueueManager(margin_threshold=0.25)

        c1 = PTCHITLCandidate(
            candidate_id="cand_db_01",
            question="Select schema for event log",
            criticality="P0",
            options=[
                PTCHITLOption(option_id="opt_wal", title="WAL Mode", raw_confidence=0.60, pattern_key="storage:wal"),
                PTCHITLOption(option_id="opt_rollback", title="Rollback Journal", raw_confidence=0.55, pattern_key="storage:rollback"),
            ],
        )
        c2 = PTCHITLCandidate(
            candidate_id="cand_db_02",
            question="Select schema for sidecar cache",
            criticality="P1",
            options=[
                PTCHITLOption(option_id="opt_wal_sidecar", title="WAL Mode", raw_confidence=0.65, pattern_key="storage:wal"),
                PTCHITLOption(option_id="opt_rollback_sidecar", title="Rollback Journal", raw_confidence=0.50, pattern_key="storage:rollback"),
            ],
        )

        qm.load_candidates([c1, c2])
        self.assertEqual(qm.get_remaining_ambiguous_count(), 2)

        # Atomic Pop: pops P0 item strictly one at a time
        popped = qm.pop_next_atomic()
        self.assertEqual(popped["candidate_id"], "cand_db_01")
        self.assertEqual(popped["criticality"], "P0")

        # Operator resolves candidate 1 affirming WAL mode with a 1.25x boost
        res = qm.record_operator_resolution(
            candidate_id="cand_db_01",
            selected_option_id="opt_wal",
            boost_factor=1.25,
            penalty_factor=0.85,
        )

        # Dynamic recalibration: Candidate 2 should be automatically promoted
        # (0.65 * 1.25) - (0.50 * 0.85) = 0.8125 - 0.4250 = 0.3875 >= 0.25
        self.assertEqual(res["promoted_by_recalibration"], 1)
        self.assertEqual(qm.get_remaining_ambiguous_count(), 0)
        self.assertEqual(len(qm.get_auto_resolved()), 1)
        self.assertEqual(qm.get_auto_resolved()[0]["winner_option_id"], "opt_wal_sidecar")

    def test_e2e_ptc_pcfs_live_fast_path_fusion_and_cockpit_streaming_in_sandbox(self) -> None:
        mock_retrieval = MagicMock()
        mock_fact = MagicMock()
        mock_fact.namespace = "CLIENT_SAFE"
        mock_fact.to_dict.return_value = {
            "node_id": "sys_maximo",
            "name": "IBM Maximo Asset Management",
            "snippet": "Core enterprise system of record for asset telemetry.",
        }
        mock_retrieval.search.return_value = [mock_fact]

        fusion_engine = CopilotFusionEngine(retrieval_engine=mock_retrieval)

        class FusionProxy:
            def __init__(self, engine: CopilotFusionEngine):
                self.engine = engine

            def stream_cockpit(self, query, live_events):
                return self.engine.stream_programmatic_fusion(
                    query=query,
                    live_fast_path_events=live_events,
                    sliding_window_size=3,
                    stealth_mode=True,
                )

        script = """
def run(proxy):
    events = [
        {"event_id": "ev1", "speaker": "Alex", "text": "Can Maximo handle 50k events/sec?"},
        {"event_id": "ev2", "speaker": "Tanner", "text": "We need the event hub buffer in front."},
        {"event_id": "ev3", "speaker": "Alex", "text": "Understood, that isolates the database."}
    ]
    return proxy.stream_cockpit("Maximo Ingestion Architecture", events)
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_pcfs_fusion",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"proxy": FusionProxy(fusion_engine)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)

        data = result.data
        self.assertTrue(data["law3_verified"])
        self.assertEqual(len(data["fast_path_context"]), 3)

        # Verify Law 3 invariant: all events marked untrusted and ephemeral
        for ev in data["fast_path_context"]:
            self.assertTrue(ev["untrusted"])
            self.assertTrue(ev["ephemeral"])

        # Verify settled canonical facts reconciled
        self.assertEqual(len(data["reconciled_canonical_facts"]), 1)
        self.assertEqual(data["reconciled_canonical_facts"][0]["name"], "IBM Maximo Asset Management")

        # Verify briefing snippet contains both settled and ambient
        snippet = data["executive_briefing_snippet"]
        self.assertIn("Settled Architecture Baseline", snippet)
        self.assertIn("Ambient Fast-Path (Unverified Live STT)", snippet)
        self.assertIn("Alex", snippet)

        # Telemetry: Compare baseline polling loop (~20KB) vs PTC single-turn stream (<2KB)
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_pcfs_fusion",
            baseline_bytes=20_480,
            spooled_summary_bytes=result.payload_bytes,
            execution_time_ms=result.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.80)


if __name__ == "__main__":
    unittest.main()
