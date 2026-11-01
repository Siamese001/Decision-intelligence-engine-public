"""
tests/test_u0_ingestion_hitl_mandatory_enforcement.py

Comprehensive E2E and Unit Verification Suite for Stage U0 Ingestion HITL Governance.
Validates:
1. Zero Bypass Invariant: All forbidden bypass flags and disabling flags fail closed immediately with ValueError.
2. Atomic Presentation Invariant: Decisions presented 1-by-1 with structured options and confidence tiers.
3. Ambiguity Gating Invariant: Autonomous progression under audit receipt when Δ ≥ 25%; HITL required when Δ < 25%.
4. Zero Silent Fallback Invariant: RosterReconcilerAgent fails closed with HITLRequiredError on unhandled ambiguity.
5. Full compliance with validate_hitl_presentation().
"""
from __future__ import annotations

import asyncio
import tempfile
from typing import Any, Dict

import pytest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.hitl_governance import validate_hitl_presentation
from dual_pipeline.speaker_attribution_core import AudioSpeakerAttributionEngine
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_qa_core.gate1_attribution import (
    check_ambiguity_lock_register_and_speaker_hitl,
)
from dual_pipeline.transcript_stages import (
    C0RosterContextGate,
    HITLRequiredError,
    U0IngestionBoundary,
)
from dual_pipeline.transcript_stages_core.c0_gates import _check_single_role_hitl
from dual_pipeline.transcript_stages_core.hitl_guard import (
    FORBIDDEN_U0_HITL_BYPASS_KEYS,
    assert_no_u0_hitl_bypass,
)
from dual_pipeline.transcript_stages_core.l1_attribution import check_generic_speakers
from pipeline_a_intake.agents.ingestion_coordinator import IngestionCoordinator
from pipeline_a_intake.agents.roster_reconciler_agent import RosterReconcilerAgent

# =============================================================================
# 1. Zero Bypass Fail-Closed Invariants
# =============================================================================

class TestZeroBypassFailClosed:
    """Certifies that zero code paths exist to bypass or disable Stage U0 HITL."""

    @pytest.mark.parametrize("key", sorted(FORBIDDEN_U0_HITL_BYPASS_KEYS))
    def test_guard_rejects_all_forbidden_bypass_keys(self, key: str) -> None:
        payload = {key: True}
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            assert_no_u0_hitl_bypass(payload)

    def test_guard_rejects_nested_bypass_keys(self) -> None:
        payload = {"options": {"deep": {"config": {"bypass_speaker_hitl": True}}}}
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            assert_no_u0_hitl_bypass(payload)

    @pytest.mark.parametrize("flag", ["enable_hitl", "interactive_hitl", "require_hitl"])
    def test_guard_rejects_disabling_flags(self, flag: str) -> None:
        payload = {flag: False}
        with pytest.raises(ValueError, match="HITL cannot be disabled in Stage U0 ingestion"):
            assert_no_u0_hitl_bypass(payload)

    def test_u0_ingestion_boundary_fails_closed_on_bypass(self) -> None:
        u0 = U0IngestionBoundary()
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            u0.execute("Dummy transcript text", {"bypass_speaker_hitl": True})

    def test_c0_roster_gate_fails_closed_on_bypass(self) -> None:
        gate = C0RosterContextGate()
        ctx = {"metadata": {"bypass_hitl": True, "roster": [{"name": "Alice"}]}}
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            gate.execute(ctx)

    def test_raw_transcript_pipeline_fails_closed_on_bypass(self) -> None:
        pipeline = RawTranscriptPipeline(provider_type="offline")
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w") as f:
            f.write("Alice [00:00:00]: Hello team.\n")
            f.flush()
            with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
                pipeline.run(f.name, metadata={"bypass_speaker_hitl": True})

    def test_ingestion_coordinator_fails_closed_on_bypass(self) -> None:
        coordinator = IngestionCoordinator(mock_mode=True)
        task = A2ATask(
            task_id="task-coord-bypass",
            parent_task_id=None,
            sender_agent_id="test-runner",
            recipient_agent_id=coordinator.card.agent_id,
            objective="test bypass rejection",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"bypass_speaker_hitl": True},
        )
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            asyncio.run(coordinator.handle_task(task))

    def test_roster_reconciler_fails_closed_on_bypass(self) -> None:
        agent = RosterReconcilerAgent(mock_mode=True)
        task = A2ATask(
            task_id="task-roster-bypass",
            parent_task_id=None,
            sender_agent_id="test-runner",
            recipient_agent_id=agent.card.agent_id,
            objective="test bypass rejection",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"bypass_roster_gate": True},
        )
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            asyncio.run(agent.handle_task(task))

    def test_audio_speaker_attribution_engine_fails_closed_on_bypass(self) -> None:
        engine = AudioSpeakerAttributionEngine()
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            engine.analyze_and_resolve(
                transcript="Speaker A: Hello.",
                metadata={"bypass_speaker_hitl": True},
            )

    def test_check_generic_speakers_fails_closed_on_bypass(self) -> None:
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            check_generic_speakers(
                "Speaker A: Hello.",
                speaker_map={"Speaker A": "Alice"},
                bypass_hitl=True,
            )

    def test_check_alr_speaker_hitl_fails_closed_on_bypass(self) -> None:
        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            check_ambiguity_lock_register_and_speaker_hitl(
                "### Ambiguity Lock Register\n| Speaker | Role |\n| Alice | Lead |\n",
                meta={"bypass_speaker_hitl": True},
            )


# =============================================================================
# 2. Atomic Optionated Confidence Invariants
# =============================================================================

class TestAtomicOptionatedConfidence:
    """Certifies that all HITL prompts are atomic 1-by-1 with calibrated options & confidence."""

    def test_c0_single_role_hitl_options_format_and_governance(self) -> None:
        meta: Dict[str, Any] = {}
        with pytest.raises(HITLRequiredError) as exc_info:
            _check_single_role_hitl("Unknown Person", "Participant", meta)

        err = exc_info.value
        assert err.hitl_type == "role_clarification"
        assert len(err.options) >= 2

        # Verify options comply with HITL presentation governance
        errs = validate_hitl_presentation({"question": err.prompt, "options": err.options})
        assert errs == [], f"Validation errors in role HITL options: {errs}"

        # Verify recommended option designated
        rec_opts = [o for o in err.options if o.get("is_recommended")]
        assert len(rec_opts) == 1
        assert "(Recommended)" in rec_opts[0]["label"]

        # Verify all options declare explicit confidence percentages & tiers
        for opt in err.options:
            assert "confidence" in opt
            assert "confidence_level" in opt
            assert opt.get("tier") in ("HIGH", "MEDIUM", "LOW")

    def test_c0_roster_gate_options_format_and_governance(self) -> None:
        gate = C0RosterContextGate()
        with pytest.raises(HITLRequiredError) as exc_info:
            gate.execute({"metadata": {"platform": "teams"}})

        err = exc_info.value
        assert err.hitl_type == "roster_gate"
        assert len(err.options) >= 2

        errs = validate_hitl_presentation({"question": err.prompt, "options": err.options})
        assert errs == [], f"Validation errors in roster gate options: {errs}"

        rec_opts = [o for o in err.options if o.get("is_recommended")]
        assert len(rec_opts) == 1
        assert "(Recommended)" in rec_opts[0]["label"]

    def test_roster_reconciler_candidate_options_governance(self) -> None:
        agent = RosterReconcilerAgent(mock_mode=True)
        candidates = agent._find_candidates("Alex", [{"name": "Alex Johnson"}, {"name": "Alex Smith"}])
        assert len(candidates) >= 2

        errs = validate_hitl_presentation({"question": "Disambiguate speaker Alex", "options": candidates})
        assert errs == [], f"Validation errors in reconciler candidates: {errs}"

        rec_opts = [o for o in candidates if o.get("is_recommended")]
        assert len(rec_opts) == 1
        assert "(Recommended)" in rec_opts[0]["label"]


# =============================================================================
# 3. Ambiguity Margin & Autonomous Approval Invariants
# =============================================================================

class TestAmbiguityMarginAndAutonomousProgression:
    """Certifies that Δ ≥ 25% proceeds autonomously and Δ < 25% enforces HITL."""

    def test_role_prior_auto_resolves_when_margin_ge_25(self) -> None:
        meta: Dict[str, Any] = {}
        # Amit Ayer has a system directory entry (Lead Enterprise Architect, Δ=57% ≥ 25%)
        _check_single_role_hitl("Amit Ayer", "Participant", meta)

        auto_dec = meta.get("autonomous_hitl_role_decisions", {}).get("Amit Ayer")
        assert auto_dec is not None
        assert auto_dec["gating"] == "AUTONOMOUS_APPROVED"
        assert auto_dec["resolved_role"] == "Lead Client Partner"
        assert auto_dec["resolution_basis"] == "directory_prior"

    def test_roster_reconciler_auto_resolves_when_decisive_margin(self) -> None:
        agent = RosterReconcilerAgent(mock_mode=False)
        task = A2ATask(
            task_id="task-decisive-margin",
            parent_task_id=None,
            sender_agent_id="test",
            recipient_agent_id=agent.card.agent_id,
            objective="test decisive margin",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "speaker_turns": [{"speaker": "Amit", "text": "Good morning."}],
                # Single exact match vs distant match -> Δ > 25%
                "roster_context": [
                    {"name": "Amit Ayer", "entity_id": "person:amit_ayer"},
                    {"name": "Zachary Taylor", "entity_id": "person:zachary_taylor"},
                ],
            },
        )
        res = asyncio.run(agent.handle_task(task))
        assert res["status"] == "COMPLETED"
        assert "Amit" in res["resolved_speakers"]
        assert res["resolved_speakers"]["Amit"]["canonical_name"] == "Amit Ayer"


# =============================================================================
# 4. Zero Silent Fallback Invariants
# =============================================================================

class TestZeroSilentFallback:
    """Certifies that RosterReconcilerAgent never silently defaults to candidates[0]."""

    def test_ambiguous_candidates_without_callback_raises_hitl_required_error(self) -> None:
        # In non-mock mode with no callback, ambiguous candidates must raise HITLRequiredError
        agent = RosterReconcilerAgent(mock_mode=False)
        task = A2ATask(
            task_id="task-ambiguous-no-fallback",
            parent_task_id=None,
            sender_agent_id="test",
            recipient_agent_id=agent.card.agent_id,
            objective="test zero silent fallback",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "speaker_turns": [{"speaker": "Alex", "text": "Hello."}],
                "roster_context": [
                    {"name": "Alex Johnson", "entity_id": "person:alex_johnson"},
                    {"name": "Alex Smith", "entity_id": "person:alex_smith"},
                ],
            },
        )
        with pytest.raises(HITLRequiredError, match="Mandatory HITL decision required for speaker 'Alex'"):
            asyncio.run(agent.handle_task(task))
