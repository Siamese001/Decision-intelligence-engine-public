"""
tests/test_judge_gate_ssot_alignment.py

Tests alignment between Gate 2 deterministic QA and ExecutiveRecapJudge
against the Declarative SSOT Output 2 Contract (OUTPUT2_CONTRACT).
"""
from __future__ import annotations

import unittest

from dual_pipeline.llm_judge_core.recap import ExecutiveRecapJudge

from dual_pipeline.prompts import (
    OUTPUT2_CONTRACT,
    MeetingArchetype,
)
from dual_pipeline.transcript_qa_core.gate2 import _check_mirrored_tables, run_qa_gate_2


class TestJudgeGateSSOTAlignment(unittest.TestCase):
    """Test Gate 2 and ExecutiveRecapJudge alignment with OUTPUT2_CONTRACT."""

    def setUp(self) -> None:
        self.tech_sync_recap = """# Subject: Technical Architecture Sync -- Executive Recap | 09/18/2026

**Date:** 09/18/2026
**Time:** 12:43 PM - 01:39 PM ET
**Attendees:**
- PartnerAdvisory Engineering: Jake (Engineer), Amit Ayer (Lead Architect), Miles (Senior Engineer)

---

## Meeting Objective
Review Project Lens automated intake pipelines, evaluate audio transcription latency bottlenecks, and design decoupled streaming state management.

## Key Discussion Points & Findings
Engineering leads reviewed the Project Lens audio transcription pipeline, evaluating transcription latency bottlenecks and memory consumption during high-concurrency ingestion. Jake demonstrated that processing long audio segments synchronously causes memory spikes and pipeline stalls. The team agreed that chunking audio into 30-second windows and processing them through decoupled worker pools resolves the bottleneck without degrading transcription fidelity.

Amit Ayer presented the state management refactoring, highlighting the need to isolate interim mutable transcription states from immutable locked final records. Miles confirmed that using monotonic segment identifiers prevents out-of-order writes during concurrent processing. The team walked through the storage architecture, certifying that SQLite connections in WAL mode with a 30-second busy timeout eliminate database contention under parallel workloads.

Detailed benchmarking demonstrated that local Whisper processing achieves real-time factors below 0.15 on Apple Silicon Metal acceleration. The team confirmed that automated health probes and memory watchers will restart long-running worker processes if heap consumption exceeds 2 gigabytes, ensuring long-term operational resilience.

## Recommendations & Decisions Required
- **Audio Chunking Architecture**: Formally adopt 30-second windowing as the mandatory ingestion unit for all audio files exceeding 10 minutes, preventing heap exhaustion.
- **State Monotonicity**: Require monotonic segment sequencing across all streaming transcription interfaces to isolate speculative interim state from locked immutable history.
- **Worker Timeout Thresholds**: Establish a 30-second SQLite busy timeout on all transactional writers to eliminate lock contention under peak concurrency.

## Action & Governance Ledger
| # | Type | Description | Owner | Target Timing | Status |
|---|---|---|---|---|---|
| 1 | Action | Implement 30-second audio chunking worker pool | Jake | 09/25/2026 | Pending |
| 2 | Blocker | Benchmark Metal memory footprint under concurrent stress | Miles | 09/22/2026 | Active |
| 3 | Gate | Verify zero data loss during simulated worker crash | Amit Ayer | 09/29/2026 | Planned |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer (Lead Architect, PartnerAdvisory)
- **Streaming State Architecture & Governance**: Amit Ayer guided the technical discussion on dual-state streaming lifecycle management, arguing that separating interim speculative state from immutable locked final transcript segments is paramount for database integrity. He emphasized that direct raw SQLite connections in worker threads violate architectural invariants and must be replaced by the governed connection manager.
- **Resilience & Fault Tolerance**: Amit highlighted the requirement for monotonic segment IDs to prevent race conditions during distributed audio processing, ensuring that session recovery remains deterministic even under unexpected process termination.

### Jake (Engineer, PartnerAdvisory)
- **Audio Processing Performance**: Jake walked through the performance profiling of long-form audio files, showing that unchunked audio inputs lead to progressive memory growth and buffer starvation in the transcription service. He advocated for fixed 30-second windowing with overlap detection to balance latency and boundary word accuracy.
- **Worker Concurrency**: Jake presented benchmark data on concurrent worker processes, confirming that thread pools scale linearly up to the physical core count before experiencing lock contention.

### Miles (Senior Engineer, PartnerAdvisory)
- **Storage Subsystem Contention**: Miles detailed the disk I/O characteristics of concurrent write transactions, noting that SQLite busy timeouts must be set to 30 seconds to prevent spurious aborts during batch extraction runs.
- **Memory Watcher Implementation**: Miles proposed automated heap thresholds for worker recycling, ensuring that long-running ingestion sessions cannot cause system-wide memory exhaustion.
"""

    def test_archetype_detection_from_recap(self) -> None:
        arch = OUTPUT2_CONTRACT.detect_archetype(text=self.tech_sync_recap)
        self.assertEqual(arch, MeetingArchetype.ARCHITECTURE_DEEP_DIVE)

    def test_tech_sync_passes_gate2_without_consulting_wedge(self) -> None:
        res = run_qa_gate_2(self.tech_sync_recap, metadata={"duration_minutes": 25})
        self.assertEqual(res["status"], "PASS", f"Gate 2 failed: {res.get('failures')}")

    def test_tech_sync_fails_gate2_if_consulting_wedge_injected(self) -> None:
        wedge_recap = self.tech_sync_recap + """
## Immediate Delivery Scope & Release Boundaries
In scope: consulting delivery wedge. Out of scope: enterprise deployment.
"""
        res = run_qa_gate_2(wedge_recap, metadata={"duration_minutes": 25})
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("GATE_2_FORBIDDEN_SECTION_DETECTED" in f for f in res.get("failures", [])))

    def test_mirrored_tables_detected_by_gate2_and_judge(self) -> None:
        mirrored_recap = self.tech_sync_recap.replace(
            "## Action & Governance Ledger",
            "## Next Steps & Action Items\n| 1 | Task | Owner | Due |\n|---|---|---|---|\n| 1 | Step | Amit | 09/25/2026 |\n\n"
            "## Governed Decision Gates\n| # | Gate | Owner | Due |\n|---|---|---|---|\n| 1 | Step | Amit | 09/25/2026 |"
        )
        # Gate 2 check
        ok, failures = _check_mirrored_tables(mirrored_recap)
        self.assertFalse(ok)
        self.assertTrue(any("GATE_2_MIRRORED_TABLES_DETECTED" in f for f in failures))

        # ExecutiveRecapJudge preflight check
        judge = ExecutiveRecapJudge()
        judge_res = judge.evaluate(f2_text=mirrored_recap, f1_text="Forensic transcript", mock=True)
        self.assertEqual(judge_res["verdict"], "FAIL")
        self.assertIn("REDUNDANCY FAILURE", judge_res["forensic_rationale"])

    def test_tech_sync_passes_recap_judge_in_mock(self) -> None:
        judge = ExecutiveRecapJudge()
        res = judge.evaluate(
            f2_text=self.tech_sync_recap,
            f1_text="Forensic transcript text with Amit Ayer, Jake, and Miles",
            mock=True,
        )
        self.assertEqual(res["verdict"], "PASS")
        self.assertGreaterEqual(res["composite_score"], 95)
        self.assertEqual(res.get("meeting_archetype"), MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value)

    def test_hallucinated_date_fails_recap_judge(self) -> None:
        hallucinated = self.tech_sync_recap.replace(
            "**Date:** 09/18/2026",
            "**Date:** 01/01/2099"
        )
        judge = ExecutiveRecapJudge()
        res = judge.evaluate(
            f2_text=hallucinated,
            f1_text="Forensic transcript text from 09/18/2026",
            mock=True,
        )
        self.assertEqual(res["verdict"], "FAIL")
        self.assertIn("GROUNDING FAILURE", res["forensic_rationale"])


if __name__ == "__main__":
    unittest.main()
