"""
tests/unit/test_inband_heartbeat_and_microjudge.py

Wave 4 verification suite:
1. In-band non-blocking progress heartbeat telemetry (BackgroundHeartbeatLogger).
2. Elimination of silent API freezes via active interval ticks (<= 15s).
3. DecomposedMultiJudge.evaluate_deliverable independent micro-judge scoring.
4. Partial deliverable pass state isolation: Output 02 receives passing scorecard (>= 80)
   even when Output 03 has pending remediation / deterministic QA failure.
5. llm_judge_evaluation.json scorecard persistence and schema validity.
"""
from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge import DecomposedMultiJudge
from dual_pipeline.transcript_pipeline_core.progress import PipelineProgressReporter
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.transcript_stages_core.heartbeat_logger import BackgroundHeartbeatLogger
from dual_pipeline.transcript_stages_core.synthesizers import BackgroundHeartbeatLogger as ExportedLogger

SAMPLE_F1 = """# Meeting Transcript: Storage Engine Architecture Evaluation | 09/18/2026

**Date:** 09/18/2026

[00:00:10] Amit Ayer: Welcome everyone. Today we are evaluating storage engine architecture.
[00:00:35] Alex Sanchez: I agree. We should explore SQLite WAL mode for local zero-contention transactions.
[00:01:00] Amit Ayer: Agreed. Let's make sure the busy timeout is set to 30 seconds and tests run hermetically.
[00:01:25] Alex Sanchez: Understood. I will verify the connection pooling pragma and run concurrency benchmarks.

## Attribution Summary
| Speaker | Attributed Turns | Confidence | Anchor Cue |
| :--- | :--- | :--- | :--- |
| Amit Ayer | 2 | HIGH | Welcome everyone |
| Alex Sanchez | 2 | HIGH | I agree |

## Ambiguity Lock Register
| Term / Ambiguity | Resolution | Affected Turns |
| :--- | :--- | :--- |
| Amit Ayer | Roster match | [00:10] |
| Alex Sanchez | Roster match | [00:35] |
| WAL | Write-Ahead Logging | [00:35] |
"""

SAMPLE_F2 = """# Subject: Storage Engine Architecture Evaluation | 09/18/2026

**Date:** 09/18/2026
**Attendees:**
- Delivery Partner: Amit Ayer, Alex Sanchez

---

## Meeting Objective
Evaluate storage engine options and establish zero-contention concurrency primitives.

## Key Discussion Points & Findings
The team analyzed SQLite WAL mode [00:35] to prevent database deadlocks in concurrent evaluation runs. We confirmed that a 30-second busy timeout [01:00] eliminates lockup cascades under load while preserving fast local access.

## Architectural Trade-Off Matrix
| Proposed Option | Architectural Mechanism | Blast Radius | Team Consensus & Rationale |
| :--- | :--- | :--- | :--- |
| Option A: SQLite WAL with pool | Thread-local connections and WAL | Low: storage package only | Selected: lightweight zero-contention hermetic operation |
| Option B: Client-server DBMS | PostgreSQL external service | High: external daemon | Deferred: excess runtime dependency overhead |

## Action & Governance Ledger
| # | Type | Item | Status | Owner | Due Date | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Action | Verify connection pooling pragma | In Progress | Alex Sanchez | 09/25/2026 | Prevent multi-worker lockups |

## Recommendations & Decisions Required
The engineering team ratifies SQLite WAL mode as the standard local storage foundation. This decision balances simplicity, speed, and thread safety for multi-worker gate evaluations.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Sanchez (Solution Architect)
- **Theme 1: SQLite WAL Concurrency Primitives**: Proposed thread-local connections with WAL pragmas to eliminate deadlocks.
- **Theme 2: Concurrency Stress Verification**: Committed to running 16-worker concurrency benchmarks to validate zero-contention.
- **Theme 3: Hermetic Dependency Minimization**: Advocated against external database daemons to preserve lightweight workstation test execution.

### Amit Ayer (Lead Architect)
- **Theme 1: Governed Storage Lifecycle**: Emphasized the necessity of 30-second busy timeouts and automated connection cleanup.
- **Theme 2: Production Architectural Decoupling**: Highlighted unidirectional layer boundaries and adherence to module budget invariants.
- **Theme 3: Multi-Worker Release Gate Assurance**: Endorsed hermetic execution standards across all pipeline stages.
"""

SAMPLE_F3_VALID = json.dumps({
    "schema_version": "5.7",
    "meeting_id": "Storage_Engine_Architecture_09_18_2026",
    "classification": "internal",
    "audiences": ["engineering"],
    "evidence_index": {
        "ev_001": {
            "speaker": "Amit Ayer",
            "source_classification": "FACT",
            "text": "Evaluating storage engine architecture with SQLite WAL.",
            "locator": {"kind": "section", "value": "[00:10]"},
        },
        "ev_002": {
            "speaker": "Alex Sanchez",
            "source_classification": "FACT",
            "text": "SQLite WAL mode provides local zero-contention transactions.",
            "locator": {"kind": "section", "value": "[00:35]"},
        },
    },
    "graph_mutations": {
        "entity_upserts": [
            {
                "id": "person:amit_ayer",
                "type": "person",
                "name": "Amit Ayer",
                "mutation": "UPDATE",
                "evidence_ids": ["ev_001"],
            },
            {
                "id": "system:sqlite_storage",
                "type": "system",
                "name": "SQLite WAL Storage",
                "mutation": "CREATE",
                "evidence_ids": ["ev_001"],
            },
        ],
        "relationship_signals": [
            {
                "signal_id": "sig_001",
                "from_id": "person:amit_ayer",
                "to_id": "system:sqlite_storage",
                "relationship": "owns",
                "evidence_ids": ["ev_001"],
            }
        ],
        "decision_updates": [
            {
                "decision_id": "decision:adopt_sqlite_wal",
                "status": "APPROVED",
                "rationale": "Adopted SQLite WAL mode as primary storage layer",
                "evidence_ids": ["ev_002"],
            }
        ],
        "proposal_updates": [],
        "action_updates": [],
        "risk_updates": [],
    },
})

MALFORMED_F3 = "INVALID_NON_JSON_GRAPH_PROJECTION"


class TestBackgroundHeartbeatLogger:
    def test_synthesizer_exports_heartbeat_logger(self) -> None:
        assert ExportedLogger is BackgroundHeartbeatLogger

    def test_heartbeat_logger_emits_discrete_ticks_to_stream(self) -> None:
        buf = io.StringIO()
        logger = BackgroundHeartbeatLogger(operation_name="Stage02 Recap", interval=0.03, stream=buf)
        with logger:
            time.sleep(0.11)

        output = buf.getvalue()
        assert len(logger.emitted_ticks) >= 2
        assert "[Stage02 Recap] Active inference in progress... (elapsed:" in output
        assert "\r" not in output
        assert output.endswith("\n")

    def test_heartbeat_logger_clean_thread_teardown(self) -> None:
        buf = io.StringIO()
        logger = BackgroundHeartbeatLogger(operation_name="Fast Task", interval=0.05, stream=buf)
        logger.start()
        time.sleep(0.02)
        logger.stop()
        assert logger._thread is not None
        assert not logger._thread.is_alive()

    def test_pipeline_progress_reporter_dual_heartbeat_support(self) -> None:
        buf = io.StringIO()
        with PipelineProgressReporter(total=5, file=buf, force_mode="plain") as reporter:
            # 1. Transport hook signature (float, str)
            reporter.heartbeat(25.4, "Stage03 Graph Extraction")
            # 2. Context manager signature (str, float)
            with reporter.heartbeat("Local Subtask", interval=0.02):
                time.sleep(0.05)

        val = buf.getvalue()
        assert "Stage03 Graph Extraction (in progress...)" in val
        assert "Local Subtask" in val


class TestDecomposedMicroJudgeIsolation:
    def test_evaluate_deliverable_routes_output2_recap(self) -> None:
        judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="medium")
        card = judge.evaluate_deliverable(
            deliverable="output_2",
            content=SAMPLE_F2,
            reference_text=SAMPLE_F1,
            mock=True,
        )
        assert card["status"] == "PASS"
        assert card["verdict"] == "PASS"
        assert card["composite_score"] >= 80
        assert card.get("is_valid_scorecard") is True

    def test_evaluate_deliverable_routes_output1_and_output3(self) -> None:
        judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="medium")
        card1 = judge.evaluate_deliverable(
            deliverable="output_1",
            content=SAMPLE_F1,
            reference_text=SAMPLE_F1,
            mock=True,
        )
        assert card1["status"] == "PASS"

        card3 = judge.evaluate_deliverable(
            deliverable="output_3",
            content=SAMPLE_F3_VALID,
            reference_text=SAMPLE_F1,
            mock=True,
        )
        assert card3["status"] == "PASS"

    def test_evaluate_deliverable_rejects_unknown_id(self) -> None:
        judge = DecomposedMultiJudge(model="gpt-5.6-sol", reasoning_effort="medium")
        with pytest.raises(ValueError, match="Unknown deliverable identifier"):
            judge.evaluate_deliverable("output_99", "content")


class TestPartialPassMicroJudgeIsolationE2E:
    def test_output2_receives_passing_scorecard_when_output3_fails_qa(self, tmp_path: Path) -> None:
        """
        Critical Wave 4 Acceptance Test:
        When Output 02 passes QA while Output 03 fails QA:
        - Output 02 must NOT be short-circuited.
        - Output 02 receives an independent passing scorecard (composite_score >= 80, status=PASS).
        - Output 03 is marked failed.
        - Output 02 pass state is recorded.
        - llm_judge_evaluation.json preserves output_2_scorecard with status=PASS.
        """
        qa_rep, judge_rep, final_dec = evaluate_judge_and_release(
            f1_text=SAMPLE_F1,
            f2_text=SAMPLE_F2,
            f3_text=MALFORMED_F3,
            metadata={
                "date": "09/18/2026",
                "meeting_name": "Storage Engine Architecture Evaluation",
                "attendees": ["Amit Ayer", "Alex Sanchez"],
                "speaker_roles": {
                    "Amit Ayer": "Lead Architect",
                    "Alex Sanchez": "Solution Architect",
                },
            },
            mock=True,
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            auto_heal=True,
        )

        # 1. QA Gate checks: Output 02 passes, Output 03 fails
        assert qa_rep["output_02_executive_recap"]["passed"] is True
        assert qa_rep["output_03_canonical_signal_projection"]["passed"] is False

        # 2. Output 02 Micro-Judge Scorecard Isolation
        assert "output_2_scorecard" in judge_rep
        o2_card = judge_rep["output_2_scorecard"]
        assert o2_card["status"] == "PASS"
        assert o2_card["verdict"] == "PASS"
        assert o2_card["composite_score"] >= 80

        # 3. Output 03 correctly marked failed without ungrounded judging
        assert "output_3_scorecard" in judge_rep
        o3_card = judge_rep["output_3_scorecard"]
        assert o3_card["verdict"] == "FAIL"
        assert o3_card["composite_score"] == 0

        # 4. Final Decision state verification
        assert final_dec["output_2_pass"] is True
        assert final_dec["output_2_deterministic_gates_pass"] is True
        assert final_dec["output_2_llm_score_pass"] is True
        assert final_dec["output_3_pass"] is False
        assert final_dec["final_pass"] is False
        assert final_dec["status"] == "FAIL"
        assert final_dec["authoritative_write_authorized"] is False

        # 5. Persisted JSON serialization verification
        judge_json_path = tmp_path / "llm_judge_evaluation.json"
        judge_json_path.write_text(json.dumps(judge_rep, indent=2), encoding="utf-8")

        loaded = json.loads(judge_json_path.read_text(encoding="utf-8"))
        assert loaded["output_2_scorecard"]["status"] == "PASS"
        assert loaded["output_2_scorecard"]["composite_score"] >= 80
        assert loaded["output_3_scorecard"]["composite_score"] == 0
