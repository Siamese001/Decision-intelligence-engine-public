"""Wave 3 adversarial & edge-case tests: Storage Law 4 assertion bindings, Intake A2A failure propagation, ALR fabrication prevention, and RetrievalCoordinator gate defaults."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus, DelegationBudget
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator
from pipeline_a_intake.agents.forensic_normalizer_agent import ForensicNormalizerAgent
from pipeline_a_intake.agents.ingestion_coordinator import IngestionCoordinator
from storage.database import AssertionRecord, DatabaseManager


def test_storage_upsert_assertion_rejects_empty_or_blank_evidence_bindings(tmp_path) -> None:
    """Category: Invariant bypass attempt — upsert_assertion must reject empty/blank evidence_bindings."""
    mgr = DatabaseManager(base_dir=tmp_path)
    asrt = AssertionRecord(
        assertion_id="asrt-ungrounded-01",
        subject_node_id="system:sherpa",
        predicate="HAS_STATUS",
        literal_value="Active",
        assertion_type="FACT",
        confidence=0.9,
        status="CONFIRMED",
    )
    with pytest.raises(ValueError, match="Law 4 ClaimCheck Violation"):
        mgr.upsert_assertion(asrt, evidence_bindings=[])

    with pytest.raises(ValueError, match="Law 4 ClaimCheck Violation"):
        mgr.upsert_assertion(asrt, evidence_bindings=[("", "   ")])

    with pytest.raises(ValueError, match="Law 4 ClaimCheck Violation"):
        mgr.upsert_assertion(asrt, evidence_bindings=None, require_evidence=True)

    assert mgr.query_assertions(subject_node_id="system:sherpa") == []


def test_ingestion_coordinator_fails_closed_on_healer_or_child_task_failure() -> None:
    """Category: Rollback & failure propagation — failed healer or child A2A task must raise instead of returning unhealed output."""
    broker = MagicMock()
    coord = IngestionCoordinator(broker=broker, mock_mode=True)
    failed_healer = MagicMock()
    failed_healer.heal_transcript = AsyncMock(
        return_value=SimpleNamespace(
            is_success=False,
            healed_artifact="",
            error_message="Healer budget exhausted",
        )
    )
    coord.healer_agent = failed_healer

    unhealed_md = "# Sync\n**Date:** 2026-09-23\n**Speaker A:** [00:05] Hello."
    with pytest.raises(ValueError, match="L1 transcript healing failed"):
        asyncio.run(coord._heal_transcript_if_needed(unhealed_md, ["Amit Ayer"]))

    failed_audio_task = A2ATask(
        task_id="t-audio",
        parent_task_id="t-root",
        sender_agent_id="coord",
        recipient_agent_id="audio",
        objective="Extract audio",
        status=A2ATaskStatus.FAILED,
        error_message="ASR crash",
    )
    ok_slide_task = A2ATask(
        task_id="t-slide",
        parent_task_id="t-root",
        sender_agent_id="coord",
        recipient_agent_id="slide",
        objective="Extract slides",
        status=A2ATaskStatus.COMPLETED,
        output_payload={"slides": [], "slide_count": 0},
    )
    broker.fan_out_and_wait = AsyncMock(return_value=[failed_audio_task, ok_slide_task])
    root_task = A2ATask(
        task_id="t-root",
        parent_task_id=None,
        sender_agent_id="caller",
        recipient_agent_id=coord.card.agent_id,
        objective="Ingest meeting",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "meeting_name": "Architecture Sync",
            "meeting_date": "09/23/2026",
            "roster_context": ["Amit Ayer"],
        },
        budget=DelegationBudget(timeout_seconds=30),
    )
    with pytest.raises(RuntimeError, match="Child A2A task 't-audio' failed"):
        asyncio.run(coord.handle_task(root_task))


def test_forensic_normalizer_agent_does_not_fabricate_locked_alr_when_empty() -> None:
    """Category: Boundary payload — empty ambiguity_locks must not fabricate a LOCKED ALR-01 row."""
    agent = ForensicNormalizerAgent()
    task = A2ATask(
        task_id="norm-1",
        parent_task_id=None,
        sender_agent_id="coord",
        recipient_agent_id=agent.card.agent_id,
        objective="Normalize transcript",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "meeting_name": "Architecture Sync",
            "meeting_date": "09/23/2026",
            "reconciled_turns": [
                {"speaker": "Amit Ayer", "start_ms": 1000, "end_ms": 4000, "text": "Let us begin."}
            ],
            "ambiguity_locks": [],
        },
    )
    res = asyncio.run(agent.handle_task(task))
    md = res["artifact_01_markdown"]
    assert "| None | None |" in md
    assert "`LOCKED`" not in md


def test_retrieval_coordinator_defaults_release_passed_to_false() -> None:
    """Category: Malformed input — missing all_gates_passed in retrieval output must default release_passed to False."""
    coord = RetrievalCoordinator(broker=MagicMock())
    coord.handle_task = AsyncMock(return_value={"fused_facts": [{"id": "f1"}], "latency_ms": 12.0})  # type: ignore[method-assign]
    res = coord.retrieve(query="What is LAMS?", candidate_facts=[])
    assert res["release_passed"] is False
