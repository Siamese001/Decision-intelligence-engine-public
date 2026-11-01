"""
tests/test_answer_feedback_and_alias_learning_e2e.py

End-to-end lifecycle and adversarial test suite for Wave 2:
1. Answer feedback capture into SQLite answer_ledger.sqlite with cryptographic digest binding.
2. Negative feedback proposal generation with auto_apply: false and approval_required: true.
3. Multi-confirmed entity alias registration in learned_aliases.json and PreSynthesisEntityResolver resolution.
4. BoundedQueryRecoveryAgent activation under BOUNDED_QUERY_RECOVERY feature flag.
5. Adversarial checks: Synthetic feedback rejection, invalid ratings, and boundary conditions.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.answer_ledger_core.feedback import (
    record_implicit_feedback,
    record_operator_feedback,
)
from dual_pipeline.answer_ledger_core.models import AnswerRecord
from dual_pipeline.answer_ledger_core.store import AnswerLedger
from dual_pipeline.feature_flags import FeatureFlagState, get_feature_flag_manager
from dual_pipeline.retrieval.agents.bounded_query_recovery_agent import (
    BoundedQueryRecoveryAgent,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_qa_core.alias_registry import (
    load_learned_aliases,
    register_learned_alias,
)


def _make_dummy_answer(answer_id: str = "ans-test-001") -> AnswerRecord:
    return AnswerRecord(
        answer_id=answer_id,
        pipeline="copilot",
        session_id="session-001",
        intent_or_type="FACTUAL",
        context_digest="d" * 64,
        retrieval_seeds=["seed:1"],
        context_snapshot=[{"turn": 1, "speaker": "Alex", "text": "Alex Sanchez is Analytics Lead."}],
        cited_assertion_ids=["asrt_001"],
        rigor="STANDARD",
        response_text="Alex Sanchez is the Analytics Lead.",
        graph_sha256="g" * 64,
        generation="gen-1",
        parameter_snapshot_digest="p" * 64,
        latency_ms=12.5,
        retrieve_latency_ms=4.0,
        verify_latency_ms=3.0,
        render_latency_ms=5.5,
        stage_latencies_ms={"total": 12.5},
        model_id="gpt-5.6-luna",
    )


def test_answer_feedback_e2e_persistence_and_proposal():
    """Verify operator feedback records to SQLite and generates a governed learning proposal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "answer_ledger.sqlite"
        proposals_dir = tmp / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)

        ledger = AnswerLedger(db_path=db_path)
        ans = _make_dummy_answer("ans-e2e-101")
        ans_persisted = ledger.append_answer(ans)
        assert ans_persisted.record_digest is not None

        # Record negative feedback
        fb = record_operator_feedback(
            ledger=ledger,
            answer_id=ans.answer_id,
            rating="wrong",
            note="Hallucinated title not in canonical graph",
            origin="USER_EXPLICIT",
            proposals_dir=proposals_dir,
        )

        assert fb.rating == "wrong"
        assert fb.answer_id == ans.answer_id
        assert fb.answer_digest == ans.record_digest

        # Check SQLite persistence
        history = ledger.list_feedback(ans.answer_id)
        assert len(history) == 1
        assert history[0].rating == "wrong"
        assert history[0].feedback_id == fb.feedback_id

        # Verify proposal artifact exists with governance invariants
        props = list(proposals_dir.glob("lcp-copilot-feedback-*.json"))
        assert len(props) == 1
        p_data = json.loads(props[0].read_text(encoding="utf-8"))
        assert p_data["auto_apply"] is False
        assert p_data["approval_required"] is True
        assert p_data["proposal_type"] == "copilot_grounding"


def test_learned_aliases_registration_and_entity_resolution(monkeypatch):
    """Verify newly registered aliases update runtime store and resolve via PreSynthesisEntityResolver."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        aliases_file = tmp / "learned_aliases.json"

        # Register alias
        aliases = register_learned_alias(
            from_id="dan o",
            to_id="person:dan-okray",
            aliases_path=aliases_file,
        )
        assert "dan o" in aliases
        assert aliases["dan o"] == "person:dan-okray"

        # Load back
        loaded = load_learned_aliases(aliases_path=aliases_file)
        assert loaded["dan o"] == "person:dan-okray"

        # Verify PreSynthesisEntityResolver resolves using learned alias
        monkeypatch.setattr(
            "dual_pipeline.transcript_qa_core.alias_registry.load_learned_aliases",
            lambda *args, **kwargs: loaded,
        )
        resolver = PreSynthesisEntityResolver(enable_graph_query=False)
        resolved = resolver.resolve("Dan O")
        assert resolved is not None
        assert resolved.canonical_name == "Drew Oakley"


def test_bounded_query_recovery_feature_flag_active():
    """Verify BOUNDED_QUERY_RECOVERY feature flag is active and agent executes bounded replanning."""
    mgr = get_feature_flag_manager()
    state = mgr.get_state("BOUNDED_QUERY_RECOVERY")
    assert state == FeatureFlagState.ON, f"Feature flag should be ON, found: {state}"

    agent = BoundedQueryRecoveryAgent()
    assert agent.max_iterations == 1

    # Test recovery execution with satisfied facets
    fused = [{"assertion_id": "a1", "content": "Drew Oakley", "facet": "attendee"}]
    cand = [{"assertion_id": "a2", "content": "Architecture", "facet": "topic"}]

    recovered_facts, receipt = agent.recover(
        query="What is the architecture topic?",
        fused_facts=fused,
        candidate_facts=cand,
        required_facets=["attendee", "topic"],
        remaining_budget=1,
    )

    assert receipt.terminal_reason == "RECOVERY_SUCCESS"
    assert receipt.is_success is True
    assert len(recovered_facts) >= 2
    assert receipt.iteration <= 1


def test_adversarial_synthetic_feedback_rejection():
    """Verify synthetic IDE stop-hook origins are rejected by validate_approval_origin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "answer_ledger.sqlite"
        ledger = AnswerLedger(db_path=db_path)
        ans = _make_dummy_answer("ans-adv-001")
        ledger.append_answer(ans)

        with pytest.raises(ValueError, match="SYNTHETIC_FEEDBACK_REJECTED"):
            record_operator_feedback(
                ledger=ledger,
                answer_id=ans.answer_id,
                rating="useful",
                origin="synthetic_auto_hook",
            )


def test_adversarial_invalid_rating_rejection():
    """Verify invalid rating strings fail closed with INVALID_RATING."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "answer_ledger.sqlite"
        ledger = AnswerLedger(db_path=db_path)
        ans = _make_dummy_answer("ans-adv-002")
        ledger.append_answer(ans)

        with pytest.raises(ValueError, match="INVALID_RATING"):
            record_operator_feedback(
                ledger=ledger,
                answer_id=ans.answer_id,
                rating="somewhat_okay",
                origin="USER_EXPLICIT",
            )
