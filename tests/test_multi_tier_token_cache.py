"""Unit tests for Wave 3: Multi-Provider Prompt Caching, Scoped Semantic Caching & 4-Tier Deterministic Cache."""

from __future__ import annotations

from pathlib import Path

from dual_pipeline.prompt_cache import build_anthropic_multi_breakpoint_payload
from dual_pipeline.semantic_cache_core.cache import SUPPORTED_SEMANTIC_NAMESPACES, SemanticCache
from dual_pipeline.token_optimization.multi_tier_cache import (
    ExactResponseDigestCache,
    PersistentNegativeFailureCache,
    SubgraphReadMemoCache,
    TurnWindowIncrementalCache,
)
from dual_pipeline.transcript_stages_core.gemini_context_cache import MIN_CACHEABLE_CHAR_ESTIMATE


def test_anthropic_multi_breakpoint_and_gemini_floor() -> None:
    payload = build_anthropic_multi_breakpoint_payload(
        system_blocks=["Governance constitution block"],
        tools=[{"name": "graph_lookup", "description": "Lookup entity"}],
        examples_block="Example 1: Verified attribution",
        turn_prefix_block="Turns 1..50 context",
        user_query="Normalize Turn 51",
        ttl="1h",
    )
    assert payload["breakpoints_count"] == 4
    assert payload["tools"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert payload["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert MIN_CACHEABLE_CHAR_ESTIMATE == 4096


def test_semantic_cache_extended_namespaces(tmp_path: Path) -> None:
    for ns in ("query", "l2_extraction", "micro_judge", "l2_healer"):
        assert ns in SUPPORTED_SEMANTIC_NAMESPACES
    cache = SemanticCache(tmp_path / "runtime")
    comp = cache.compatibility(
        {
            "use_case": "l2_extraction",
            "graph_generation_id": "gen_001",
            "merkle_root_hash": "a" * 64,
        }
    )
    assert comp["use_case"] == "l2_extraction"
    assert comp["graph_generation_id"] == "gen_001"


def test_exact_response_and_turn_window_caches(tmp_path: Path) -> None:
    db_path = tmp_path / "multi_tier.sqlite"
    exact = ExactResponseDigestCache(db_path)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    assert exact.get(model="gpt-5.6-astra", effort="low", messages=msgs) is None
    exact.put(
        model="gpt-5.6-astra",
        effort="low",
        messages=msgs,
        response={"answer": "cached"},
        prompt_tokens=1200,
        completion_tokens=100,
    )
    hit = exact.get(model="gpt-5.6-astra", effort="low", messages=msgs)
    assert hit == {"answer": "cached"}

    win_cache = TurnWindowIncrementalCache(db_path, window_size=2)
    turns = ["t1", "t2", "t3", "t4"]
    win_cache.store_window("mtg_1", 0, ["t1", "t2"], ["T1_NORM", "T2_NORM"])
    win_cache.store_window("mtg_1", 1, ["t3", "t4"], ["T3_NORM", "T4_NORM"])

    # Edit only turn 4 -> window 0 stays cached, window 1 is dirty
    edited_turns = ["t1", "t2", "t3", "t4_edited"]
    res = win_cache.resolve_cached_or_dirty_windows("mtg_1", edited_turns)
    assert res["cached_window_count"] == 1
    assert res["dirty_window_count"] == 1
    assert res["cached_windows"][0] == ["T1_NORM", "T2_NORM"]


def test_negative_failure_and_subgraph_memo_caches(tmp_path: Path) -> None:
    db_path = tmp_path / "multi_tier.sqlite"
    neg = PersistentNegativeFailureCache(db_path, max_failures=2)
    c1 = neg.record_failure(
        stage="L2_HEALER",
        payload_text="bad_json",
        defect_code="ERR_JSON",
        rationale="Malformed JSON",
    )
    assert c1 == 1
    q1, _ = neg.is_quarantined(stage="L2_HEALER", payload_text="bad_json")
    assert not q1

    c2 = neg.record_failure(
        stage="L2_HEALER",
        payload_text="bad_json",
        defect_code="ERR_JSON",
        rationale="Malformed JSON",
    )
    assert c2 == 2
    q2, info = neg.is_quarantined(stage="L2_HEALER", payload_text="bad_json")
    assert q2 is True
    assert info is not None and info["fail_count"] == 2

    memo = SubgraphReadMemoCache(db_path)
    memo.put(generation_id="gen_1", query_signature="entity:123", payload={"nodes": [1, 2]})
    assert memo.get(generation_id="gen_1", query_signature="entity:123") == {"nodes": [1, 2]}
    # New generation invalidates old generation memo entries
    memo.put(generation_id="gen_2", query_signature="entity:456", payload={"nodes": [3]})
    assert memo.get(generation_id="gen_1", query_signature="entity:123") is None
    assert memo.get(generation_id="gen_2", query_signature="entity:456") == {"nodes": [3]}
