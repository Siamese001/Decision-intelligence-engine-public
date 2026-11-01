"""Unit tests for BatchMicroJudgeEvaluator and AgenticLeverageJudge batching integration.

Tests offline mock evaluations, deduplication, fallback resilience, and score parity.
"""
from unittest.mock import MagicMock
import pytest

from dual_pipeline.llm_judge_core.batch_judge import (
    BatchMicroJudgeEvaluator,
    _compute_item_hash,
    _item_to_dict,
)
from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge
from dual_pipeline.llm_judge_core.models import (
    BatchedEvaluationResult,
    JudgeAgenticLeverage,
)


@pytest.fixture
def sample_nodes():
    return [
        {
            "speaker": "Alice",
            "key_players": ["Sarah VP of Cloud", "Bob Director"],
            "political_rationale": "Sarah is unaware that the enterprise architecture migration has stalled, presenting an opportunity to step in.",
            "networking_wedge": "Propose an executive risk assessment debrief directly to Sarah before the quarterly review.",
        },
        {
            "speaker": "Bob",
            "key_players": ["Bob"],
            "political_rationale": "Routine sprint task.",
            "networking_wedge": "Send acknowledgment.",
        },
        {
            "speaker": "Charlie",
            "key_players": ["Dave Managing Director"],
            "political_rationale": "Dave wants to expand the PartnerAdvisory footprint across data engineering, but lacks client sponsor alignment.",
            "networking_wedge": "Schedule an alignment session with Dave highlighting key delivery wins.",
        },
    ]


@pytest.fixture
def sample_transcript():
    return (
        "Sarah (VP of Cloud): We need to understand why the migration timeline is slipping.\n"
        "Alice: The integration testing uncovered several legacy schema dependencies.\n"
        "Dave (Managing Director): If we can resolve this, we can expand our engagement across data engineering."
    )


def test_item_to_dict_and_hash():
    item = {"a": 1, "b": "test"}
    d = _item_to_dict(item)
    assert d == item
    h1 = _compute_item_hash(item)
    h2 = _compute_item_hash({"b": "test", "a": 1})
    assert h1 == h2  # Order independent due to sort_keys


def test_batch_micro_judge_mock_evaluation(sample_nodes, sample_transcript):
    evaluator = BatchMicroJudgeEvaluator(mock=True)
    res = evaluator.evaluate_batch(nodes=sample_nodes, reference_text=sample_transcript)

    assert isinstance(res, BatchedEvaluationResult)
    assert res.item_count == 3
    assert len(res.evaluated_items) == 3
    assert res.fallback_invoked is False
    assert res.estimated_tokens_saved > 0

    # Node 0 (Sarah VP) should pass
    item0_judgment = res.evaluated_items[0]["judgment"]
    assert item0_judgment["pass_gate"] is True
    assert item0_judgment["political_materiality"] >= 8

    # Node 1 (Junior Engineer) should fail
    item1_judgment = res.evaluated_items[1]["judgment"]
    assert item1_judgment["pass_gate"] is False


def test_batch_micro_judge_deduplication(sample_nodes, sample_transcript):
    # Pass 5 items where items 0 and 3, and items 1 and 4 are identical
    duplicated_nodes = [
        sample_nodes[0],
        sample_nodes[1],
        sample_nodes[2],
        sample_nodes[0],  # duplicate
        sample_nodes[1],  # duplicate
    ]
    evaluator = BatchMicroJudgeEvaluator(mock=True)
    res = evaluator.evaluate_batch(nodes=duplicated_nodes, reference_text=sample_transcript)

    assert res.item_count == 5
    assert len(res.evaluated_items) == 5
    assert res.deduplicated_count == 2
    # Ensure duplicates receive identical judgment
    assert res.evaluated_items[0]["judgment"] == res.evaluated_items[3]["judgment"]
    assert res.evaluated_items[1]["judgment"] == res.evaluated_items[4]["judgment"]


def test_batch_micro_judge_empty_input():
    evaluator = BatchMicroJudgeEvaluator(mock=True)
    res = evaluator.evaluate_batch(nodes=[], reference_text="Some text")
    assert res.item_count == 0
    assert len(res.evaluated_items) == 0


def test_batch_micro_judge_single_item(sample_nodes, sample_transcript):
    evaluator = BatchMicroJudgeEvaluator(mock=True)
    res = evaluator.evaluate_batch(nodes=[sample_nodes[0]], reference_text=sample_transcript)
    assert res.item_count == 1
    assert res.deduplicated_count == 0
    assert res.estimated_tokens_saved == 0


def test_batch_micro_judge_fallback_on_error(sample_nodes, sample_transcript):
    """Verify automatic fallback when live LLM execution fails."""
    evaluator = BatchMicroJudgeEvaluator(mock=False)
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Provider timeout simulation")

    res = evaluator.evaluate_batch(
        nodes=sample_nodes,
        reference_text=sample_transcript,
        client=mock_client,
        mock=False,
    )

    assert res.item_count == 3
    assert len(res.evaluated_items) == 3
    assert res.fallback_invoked is True


def test_agentic_leverage_judge_batched_parity(sample_nodes, sample_transcript):
    """Verify 100% score and verdict parity between batched and unbatched evaluations."""
    judge = AgenticLeverageJudge(mock=True)

    unbatched_results = judge.evaluate_agentic_nodes(
        sample_nodes, transcript_text=sample_transcript, batch=False
    )
    batched_results = judge.evaluate_agentic_nodes(
        sample_nodes, transcript_text=sample_transcript, batch=True
    )

    assert len(unbatched_results) == len(batched_results)
    for u, b in zip(unbatched_results, batched_results):
        assert u["node"] == b["node"]
        assert u["judgment"]["pass_gate"] == b["judgment"]["pass_gate"]
        assert u["judgment"]["political_materiality"] == b["judgment"]["political_materiality"]
        assert u["judgment"]["networking_target_identified"] == b["judgment"]["networking_target_identified"]
