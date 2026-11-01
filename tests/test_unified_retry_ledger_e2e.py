"""
End-to-end tests for Wave 7:
Unified Retry State Machine, Single-Pass Stage Freezing & Live Session Isolation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from dual_pipeline.canonical_pipeline.attempt_binding import BoundAttemptLedger, UnifiedAttemptLedger
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _read_attempt_checkpoint
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    _GLOBAL_ORCHESTRATOR_REGISTRY,
    _MAX_ACTIVE_SESSIONS,
    CopilotLifecycleState,
    get_or_create_lifecycle_orchestrator,
    reset_lifecycle_orchestrators,
)
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import finalize_diarization_and_seal
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import RetrievalPerspectiveHealerAgent


def test_unified_attempt_ledger_and_state_machine_monotonic_score() -> None:
    """Verify UnifiedAttemptLedger alias and SelectiveRemediationStateMachine monotonic score resolution."""
    assert UnifiedAttemptLedger is BoundAttemptLedger
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.register_deliverable_pass("output_1", "### [00:00] **Alice:** Valid turn.", score=97)
    score, verdict = sm._resolve_eval_score("output_1", {"composite_score": 0, "verdict": "FAIL"})
    assert score == 97
    assert verdict == "PASS"


def test_checkpoint_memoizer_rejects_unsealed_attempt_even_on_graph_retry(tmp_path: Path) -> None:
    """_read_attempt_checkpoint must reject unsealed attempt directories even when allow_unsealed_graph_retry=True."""
    att_dir = tmp_path / "attempt_1"
    att_dir.mkdir(parents=True)
    ckpt = att_dir / "output_1.ckpt.md"
    ckpt.write_text("### [00:00] **Alice:** Turn content with more than two hundred bytes. " * 5, encoding="utf-8")

    res = _read_attempt_checkpoint(
        att_dir,
        "output_1.ckpt.md",
        "output_1_pass",
        allow_unsealed_graph_retry=True,
    )
    assert res is None


def test_synthesis_can_reuse_healed_output_3_rejects_failed_llm_judge(tmp_path: Path) -> None:
    """_can_reuse_healed_output_3 must reject an attempt where output_3_llm_score_pass is False."""
    att_dir = tmp_path / "attempt_1"
    att_dir.mkdir(parents=True)
    cand_file = att_dir / "output_3.ckpt.json"
    cand_file.write_text('{"schema_version": "5.7", "graph_mutations": {}}', encoding="utf-8")
    (att_dir / "final_release_decision.json").write_text(
        '{"output_3_pass": false, "output_3_deterministic_gates_pass": true, "output_3_llm_score_pass": false}',
        encoding="utf-8",
    )
    (att_dir / "llm_judge_evaluation.json").write_text(
        '{"output_3_evaluation": {"failure_type": "TIMEOUT", "defect_codes": ["ERR_JUDGE_TIMEOUT"]}}',
        encoding="utf-8",
    )

    assert (
        FullMeetingSynthesisStrategy._can_reuse_healed_output_3(
            cand_file,
            '{"schema_version": "5.7", "graph_mutations": {}}',
            "### [00:00] **Alice:** Turn.",
            {"meeting_name": "Debrief Rob PartnerAdvisory"},
        )
        is False
    )


def test_retrieval_perspective_healer_does_not_inject_corpus_literals() -> None:
    """RetrievalPerspectiveHealerAgent defaults active_ontology_terms to empty list instead of hardcoded BGE-M3 literals."""
    agent = RetrievalPerspectiveHealerAgent(mock_mode=True)
    res = asyncio.run(agent.heal_starved_perspective("What are the architecture risks?", "TECHNICAL"))
    assert res.is_success
    retained = (res.healed_artifact or {}).get("retained_terms", [])
    assert "system:bge-m3" not in retained


def test_copilot_lifecycle_orchestrator_bounded_registry_and_stop_seal_errors(tmp_path: Path) -> None:
    """Verify _GLOBAL_ORCHESTRATOR_REGISTRY evicts beyond _MAX_ACTIVE_SESSIONS and stop_lifecycle records seal errors."""
    reset_lifecycle_orchestrators()
    try:
        for idx in range(_MAX_ACTIVE_SESSIONS + 5):
            orch = get_or_create_lifecycle_orchestrator(f"sess_{idx}")
            if idx == 0:
                orch.shutdown()
        assert len(_GLOBAL_ORCHESTRATOR_REGISTRY) <= _MAX_ACTIVE_SESSIONS
        assert "sess_0" not in _GLOBAL_ORCHESTRATOR_REGISTRY
    finally:
        reset_lifecycle_orchestrators()

    recorder: Any = MagicMock()
    recorder.session_manifest = tmp_path / "manifest.json"
    recorder.transcript_md = tmp_path / "transcript.md"
    recorder.acoustic_evidence_receipt_path = tmp_path / "acoustic.json"
    recorder.master_wav = None
    recorder.stop_lifecycle_errors = ["simulated_drain_error"]
    finalize_diarization_and_seal(recorder)
    assert recorder.seal_receipt["degraded"] is True
    assert "simulated_drain_error" in recorder.seal_receipt["stop_lifecycle_errors"]
