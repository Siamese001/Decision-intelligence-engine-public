"""
tests/test_ptc_context_fusion.py

Unit tests for Programmatic Context Fusion Streamer (PCFS) in Pipeline D.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ptc.context_fusion import (
    PTCContextFusionStreamer,
    PTCFusionPlan,
    PTCLiveFastPathEvent,
)
from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.sandbox import PTCSandbox
from dual_pipeline.ptc.spooler import PTCOutputSpooler


class TestPTCContextFusion(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.spool_dir = Path(self.temp_dir.name) / "spool"
        self.spooler = PTCOutputSpooler(spool_dir=self.spool_dir)
        self.streamer = PTCContextFusionStreamer(spooler=self.spooler)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_law3_untrusted_fast_path_tagging(self) -> None:
        raw_events = [
            {"event_id": "ev_1", "speaker": "Alice", "text": "We need to scale the event hub.", "untrusted": False},
            PTCLiveFastPathEvent(event_id="ev_2", speaker="Bob", text="Agreed, Kafka lag is increasing."),
        ]

        plan = PTCFusionPlan(
            query="Event Streaming Capacity",
            live_fast_path_events=raw_events,
            settled_canonical_facts=[{"node_id": "node_hub", "name": "Azure Event Hub"}],
        )

        payload = self.streamer.fuse(plan)

        self.assertTrue(payload.law3_verified)
        self.assertEqual(len(payload.fast_path_context), 2)
        # Verify Law 3 invariant: all fast path events must be flagged untrusted and ephemeral
        for ev in payload.fast_path_context:
            self.assertTrue(ev["untrusted"])
            self.assertTrue(ev["ephemeral"])

    def test_sliding_window_bounds_fast_path_events(self) -> None:
        events = [
            {"event_id": f"ev_{i}", "speaker": "Speaker", "text": f"Utterance {i}"}
            for i in range(10)
        ]

        plan = PTCFusionPlan(
            query="Streaming check",
            live_fast_path_events=events,
            sliding_window_size=4,
        )

        payload = self.streamer.fuse(plan)

        self.assertEqual(len(payload.fast_path_context), 4)
        self.assertEqual(payload.fast_path_context[0]["event_id"], "ev_6")
        self.assertEqual(payload.fast_path_context[-1]["event_id"], "ev_9")

    def test_stealth_mode_partitions_advisory_facts(self) -> None:
        canonical = [{"node_id": "c1", "name": "Canonical Arch"}]
        advisory = [{"node_id": "a1", "name": "Private Advisory Leverage"}]

        # In stealth mode (default) -> advisory facts omitted
        plan_stealth = PTCFusionPlan(
            query="Architecture Audit",
            live_fast_path_events=[],
            settled_canonical_facts=canonical,
            settled_advisory_facts=advisory,
            stealth_mode=True,
        )
        payload_stealth = self.streamer.fuse(plan_stealth)
        self.assertEqual(len(payload_stealth.reconciled_canonical_facts), 1)
        self.assertEqual(len(payload_stealth.reconciled_advisory_facts), 0)
        self.assertNotIn("Internal Advisory Lens", payload_stealth.executive_briefing_snippet)

        # When stealth mode is False -> advisory facts included
        plan_unmasked = PTCFusionPlan(
            query="Architecture Audit",
            live_fast_path_events=[],
            settled_canonical_facts=canonical,
            settled_advisory_facts=advisory,
            stealth_mode=False,
        )
        payload_unmasked = self.streamer.fuse(plan_unmasked)
        self.assertEqual(len(payload_unmasked.reconciled_canonical_facts), 1)
        self.assertEqual(len(payload_unmasked.reconciled_advisory_facts), 1)
        self.assertIn("Internal Advisory Lens", payload_unmasked.executive_briefing_snippet)

    def test_in_process_briefing_synthesis_generation(self) -> None:
        plan = PTCFusionPlan(
            query="Message Broker",
            live_fast_path_events=[
                {"event_id": "e1", "speaker": "Tanner", "text": "We chose Kafka for high throughput."}
            ],
            settled_canonical_facts=[{"node_id": "n1", "name": "Enterprise Kafka Cluster"}],
        )

        payload = self.streamer.fuse(plan)

        self.assertIn("Settled Architecture Baseline", payload.executive_briefing_snippet)
        self.assertIn("Enterprise Kafka Cluster", payload.executive_briefing_snippet)
        self.assertIn("Ambient Fast-Path (Unverified Live STT)", payload.executive_briefing_snippet)
        self.assertIn("Tanner", payload.executive_briefing_snippet)
        self.assertTrue(payload.fusion_digest.startswith("sha256:"))

    def test_token_budget_spooling_for_large_payloads(self) -> None:
        # Generate large facts that exceed 500 bytes budget
        large_facts = [
            {
                "node_id": f"node_{i:03d}",
                "name": f"System Component {i}",
                "snippet": "Extensive architectural specification detailing latency and SLA parameters." * 5,
            }
            for i in range(15)
        ]

        plan = PTCFusionPlan(
            query="Large Query",
            live_fast_path_events=[{"event_id": "ev1", "speaker": "A", "text": "Hello"}],
            settled_canonical_facts=large_facts,
            token_budget=TokenBudget(max_payload_bytes=512),
        )

        payload = self.streamer.fuse(plan)

        self.assertTrue(payload.is_spooled)
        self.assertIsNotNone(payload.spool_path)
        self.assertTrue(Path(payload.spool_path).is_file())

    def test_sandbox_executes_context_fusion(self) -> None:
        class FusionProxy:
            def __init__(self, streamer):
                self.streamer = streamer

            def fuse_stream(self, query, events, facts):
                plan = PTCFusionPlan(
                    query=query,
                    live_fast_path_events=events,
                    settled_canonical_facts=facts,
                )
                return self.streamer.fuse(plan).to_dict()

        script = """
def run(proxy):
    evs = [{"event_id": "e1", "speaker": "Tanner", "text": "Let's align on BGE-M3."}]
    facts = [{"node_id": "bge", "name": "BGE-M3 Dense-Sparse Hybrid"}]
    res = proxy.fuse_stream("Embedding Alignment", evs, facts)
    return {
        "fast_path_count": len(res["fast_path_context"]),
        "canonical_count": len(res["reconciled_canonical_facts"]),
        "law3_verified": res["law3_verified"],
        "snippet": res["executive_briefing_snippet"]
    }
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_fusion_sandbox",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"proxy": FusionProxy(self.streamer)},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["fast_path_count"], 1)
        self.assertEqual(summary.data["canonical_count"], 1)
        self.assertTrue(summary.data["law3_verified"])
        self.assertIn("BGE-M3 Dense-Sparse Hybrid", summary.data["snippet"])


if __name__ == "__main__":
    unittest.main()
