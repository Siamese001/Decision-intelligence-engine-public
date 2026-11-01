"""tests/end_to_end/test_copilot_scope_quarantine_e2e.py

End-to-end integration and lifecycle verification for:
1. Copilot graph cache keying by engagement_id and cross-engagement isolation.
2. --bypass-graph-cache end-to-end bypass and fresh retrieval persistence.
3. --intent respond speaker error tolerance with topic query fallback.
4. Reason code mapping in copilot_attempt_ledger for scope boundary,
   rejected evidence, and rendering failures.
5. Copilot failure diagnostic preservation (intent, target, topic) in attempt record.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_RENDERING_FAILURE,
    CATEGORY_REJECTED_EVIDENCE,
    CATEGORY_SCOPE_BOUNDARY,
    CATEGORY_USAGE,
    categorize_copilot_terminal_reason,
    create_attempt_record,
    record_copilot_attempt,
)
from dual_pipeline.live_meeting_recorder_core.copilot_cache_graph import (
    _read_cache_payload,
    execute_load_copilot_graph,
    load_cached_retriever,
    resolve_cache_file,
    save_cached_retriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import _copilot_failure
from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    _eval_respond_candidate,
)
from dual_pipeline.live_meeting_recorder_core.copilot_offline import (
    is_tolerated_speaker_error,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)


class DummyTopology:
    def __init__(self, graph_sha256: str = "sha-01") -> None:
        self.graph_sha256 = graph_sha256
        self.nodes = {}
        self.assertions = {}


class DummyMockRetriever:
    """Mock retriever satisfying cache save and load contracts."""

    def __init__(
        self,
        generation: str = "gen-test-01",
        graph_sha256: str = "sha-test-01",
        engagement_id: Optional[str] = None,
    ) -> None:
        self.generation = generation
        self.graph_sha256 = graph_sha256
        self.engagement_id = engagement_id
        self._topology = DummyTopology(graph_sha256)
        self._inverted_index = {}
        self._token_idf = {}
        self._salient_assertions = {"asrt-1": [{"predicate": "USES", "claim_text": "Alpha Cloud"}]}
        self._allowed_nodes = set()
        self._claim_inverted_index = {}
        self._indexed_assertion_records = {}
        self.neighborhood_store = None


@pytest.mark.e2e
def test_copilot_cache_keying_by_engagement_isolation() -> None:
    """Verifies different engagement_ids produce distinct cache files and validate expected_engagement_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = Path(tmpdir)

        # 1. Resolve distinct cache files
        cache_a = resolve_cache_file(None, cache_dir, "gen-01", "sha-01", engagement_id="project:alpha")
        cache_b = resolve_cache_file(None, cache_dir, "gen-01", "sha-01", engagement_id="project:beta")
        cache_none = resolve_cache_file(None, cache_dir, "gen-01", "sha-01", engagement_id=None)

        assert cache_a.name.endswith("_alpha.pkl")
        assert cache_b.name.endswith("_beta.pkl")
        assert not cache_none.name.endswith("_alpha.pkl")
        assert cache_a != cache_b

        # 2. Save retriever with engagement_id "project:alpha"
        retriever_a = DummyMockRetriever("gen-01", "sha-01", engagement_id="project:alpha")
        in_mem_dummy = DummyTopology("sha-01")
        save_cached_retriever(cache_a, in_mem_dummy, retriever_a)
        assert cache_a.exists()

        # 3. Read payload with matching expected_engagement_id
        loaded_a = _read_cache_payload(cache_a, "sha-01", expected_engagement_id="project:alpha")
        assert loaded_a is not None
        assert loaded_a["engagement_id"] == "project:alpha"

        # 4. Read payload with mismatched expected_engagement_id returns None
        loaded_mismatch = _read_cache_payload(cache_a, "sha-01", expected_engagement_id="project:beta")
        assert loaded_mismatch is None


@pytest.mark.e2e
def test_copilot_cache_bypass_flag() -> None:
    """Verifies bypass_cache=True in load_copilot_graph forces bypass of cache loading."""
    from dual_pipeline.live_meeting_recorder_core.copilot_retriever import load_copilot_graph

    with patch("dual_pipeline.live_meeting_recorder_core.copilot_cache_graph.load_cached_retriever") as mock_cache_loader, \
         patch("dual_pipeline.live_meeting_recorder_core.copilot_cache_graph.resolve_graph_paths_and_hash") as mock_resolve:

        fake_root = Path("/tmp/fake_runtime")
        fake_pair = fake_root / "active_pair.json"
        fake_db = fake_root / "graph.sqlite"
        pair_dict = {"generation_id": "gen-test", "graph_sha256": "hash123"}
        mock_resolve.return_value = (fake_root, fake_pair, "hash123", fake_db, pair_dict)
        mock_cache_loader.return_value = (MagicMock(), MagicMock())

        # When bypass_cache=False, cache loader is invoked
        load_copilot_graph(runtime_root=fake_root, bypass_cache=False)
        assert mock_cache_loader.called

        mock_cache_loader.reset_mock()

        # When bypass_cache=True, cache loader is skipped
        with patch("dual_pipeline.in_memory_graph.InMemoryGraphTopology.load_from_sqlite") as mock_sqlite:
            mock_sqlite.side_effect = Exception("Intentionally aborted after cache bypass")
            load_copilot_graph(runtime_root=fake_root, bypass_cache=True)
            # Cache loader MUST NOT have been called
            mock_cache_loader.assert_not_called()


@pytest.mark.e2e
def test_copilot_respond_intent_speaker_fallback_e2e() -> None:
    """Verifies that respond intent tolerates ZERO_RELEVANT_UTTERANCES when an explicit topic query is provided."""
    context = ConversationContextModel()

    # 1. ZERO_RELEVANT_UTTERANCES with topic query -> tolerated
    is_tol = is_tolerated_speaker_error(
        ("ZERO_RELEVANT_UTTERANCES", "Target speaker has no turns addressing topic"),
        intent="respond",
        topic_keywords=["churn", "knowledge"],
        topic_query="how to handle knowledge base and team churn?",
        context_model=context,
    )
    assert is_tol is True

    # 2. ZERO_RELEVANT_UTTERANCES without topic query or keywords -> rejected
    is_tol_no_topic = is_tolerated_speaker_error(
        ("ZERO_RELEVANT_UTTERANCES", "Target speaker has no turns"),
        intent="respond",
        topic_keywords=[],
        topic_query=None,
        context_model=context,
    )
    assert is_tol_no_topic is False

    # 3. Next-actions with query scope -> tolerated
    assert is_tolerated_speaker_error(
        ("ZERO_RELEVANT_UTTERANCES", "none"),
        intent="next-actions",
        topic_keywords=["action"],
        topic_query=None,
        context_model=context,
    ) is True


@pytest.mark.e2e
def test_copilot_attempt_ledger_reason_categorization_and_metadata() -> None:
    """Verifies AbstainReasonCodes map to canonical categories rather than unhandled_error."""
    # Scope boundary
    cat, msg = categorize_copilot_terminal_reason("SCOPE_QUARANTINED", "Violation details")
    assert cat == CATEGORY_SCOPE_BOUNDARY
    assert "[Scope Boundary]" in msg

    cat_c19, _ = categorize_copilot_terminal_reason("C19_CAREER_LEVERAGE")
    assert cat_c19 == CATEGORY_SCOPE_BOUNDARY

    cat_cross, _ = categorize_copilot_terminal_reason("CROSS_PROJECT_VIOLATION")
    assert cat_cross == CATEGORY_SCOPE_BOUNDARY

    # Rejected evidence
    cat_ungrounded, _ = categorize_copilot_terminal_reason("UNGROUNDED_CLAIM")
    assert cat_ungrounded == CATEGORY_REJECTED_EVIDENCE

    cat_contra, _ = categorize_copilot_terminal_reason("CONTRADICTION")
    assert cat_contra == CATEGORY_REJECTED_EVIDENCE

    # Rendering failure
    cat_budget, _ = categorize_copilot_terminal_reason("WORD_BUDGET_EXCEEDED")
    assert cat_budget == CATEGORY_RENDERING_FAILURE

    # Absent candidates
    cat_empty, _ = categorize_copilot_terminal_reason("EMPTY_CLAIM")
    assert cat_empty == CATEGORY_ABSENT_CANDIDATES

    # Usage
    cat_diar, _ = categorize_copilot_terminal_reason("LOW_SPEAKER_CONFIDENCE")
    assert cat_diar == CATEGORY_USAGE


@pytest.mark.e2e
def test_copilot_engine_failure_diagnostic_preservation() -> None:
    """Verifies copilot engine records structured attempt record with intent, target, and topic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        args = argparse.Namespace(
            request="how to position partner_advisory?",
            intent="respond",
            target="Noemi Monnerville",
            topic="team churn",
            words=35,
            session="session-uuid-123",
        )

        res = _copilot_failure(
            args=args,
            error="SCOPE_QUARANTINED",
            details="Assertion violates scope isolation",
            output_dir=out_dir,
            runtime_root=out_dir,
            intent="respond",
            target="Noemi Monnerville",
            topic="team churn",
            machine=True,
        )

        assert res["status"] == "FAIL"
        assert res["failure_category"] == CATEGORY_SCOPE_BOUNDARY
        assert "attempt_path" in res

        # Verify attempts log written with parsed fields
        attempts_file = out_dir / "copilot_attempts.jsonl"
        assert attempts_file.exists()
        lines = attempts_file.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["parsed_intent"] == "respond"
        assert rec["parsed_target"] == "Noemi Monnerville"
        assert rec["parsed_topic"] == "team churn"
        assert rec["terminal_reason"] == "SCOPE_QUARANTINED"
        assert rec["failure_category"] == "scope_boundary"
