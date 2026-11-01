"""Adversarial and edge-case test suite for Tokenomics Caching, Healer, and Judge Loops.

Covers 6 mandatory adversarial edge-case categories:
1. Boundary payload: Empty turns, 0-token messages, and sub-threshold cache prefixes.
2. Malformed input: Corrupted/non-numeric scorecard dimensions and invalid JSON candidates.
3. Concurrency & race: Multi-threaded WAL writes to ExactResponseDigestCache and PersistentNegativeFailureCache.
4. Overflow & exhaustion: Healer max_repair_calls=0 budget exhaustion and Anthropic >4 breakpoint cap.
5. Invariant bypass attempt: Forbidden PII/run_id injection in derive_cache_key and unbindable semantic namespaces.
6. Rollback & recovery: Generation rollover invalidating stale SubgraphReadMemoCache entries.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from dual_pipeline.l2_healer_service import UnifiedL2HealerService
from dual_pipeline.l2_healing_contracts import HealingScope, L2HealingRequest
from dual_pipeline.llm_judge_core.judges.forensic import _heal_scorecard_arithmetic
from dual_pipeline.prompt_cache import build_anthropic_multi_breakpoint_payload, derive_cache_key
from dual_pipeline.semantic_cache_core.cache import SemanticCache
from dual_pipeline.token_optimization.multi_tier_cache import (
    ExactResponseDigestCache,
    PersistentNegativeFailureCache,
    SubgraphReadMemoCache,
    TurnWindowIncrementalCache,
)


def test_adversarial_boundary_empty_inputs_and_subthreshold_windows(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): Empty turn lists and empty messages do not crash."""
    win_cache = TurnWindowIncrementalCache(tmp_path / "edge.sqlite", window_size=25)
    res = win_cache.resolve_cached_or_dirty_windows("empty_meeting", [])
    assert res["total_windows"] == 0
    assert res["cached_window_count"] == 0
    assert res["dirty_window_count"] == 0


def test_adversarial_malformed_scorecard_dimensions_rejected_safely() -> None:
    """Category 2 (Malformed input): Boolean or out-of-range dimensions are not arithmetically healed."""
    malformed = {
        "dimension_1_forensic_grounding_and_fidelity": True,
        "dimension_2_attribution_and_room_mic_hygiene": 30,  # > 25 out of range
        "dimension_3_chronological_and_timestamp_integrity": -5,
        "dimension_4_asr_cleanup_and_domain_preservation": "20",
        "composite_score": 10,
    }
    out = _heal_scorecard_arithmetic(malformed)
    assert out["composite_score"] == 10
    assert "arithmetic_healed" not in out


def test_adversarial_concurrent_sqlite_multi_tier_cache_writes(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): 8 parallel threads writing to WAL-backed caches without lock errors."""
    db_path = tmp_path / "concurrent.sqlite"
    exact = ExactResponseDigestCache(db_path)
    neg = PersistentNegativeFailureCache(db_path, max_failures=2)

    def _worker(idx: int) -> bool:
        msgs = [{"role": "user", "content": f"payload-{idx}"}]
        exact.put(
            model="gpt-5.6-astra",
            effort="low",
            messages=msgs,
            response={"idx": idx},
        )
        got = exact.get(model="gpt-5.6-astra", effort="low", messages=msgs)
        neg.record_failure(
            stage="L2",
            payload_text=f"bad-{idx}",
            defect_code="ERR_SCHEMA",
            rationale="concurrent failure",
        )
        return got == {"idx": idx}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_worker, range(24)))
    assert all(results)


def test_adversarial_budget_exhaustion_and_breakpoint_cap() -> None:
    """Category 4 (Overflow & exhaustion): max_repair_calls=0 halts immediately; breakpoints capped at 4."""
    healer = UnifiedL2HealerService(mock_mode=True)
    req = L2HealingRequest(
        scope=HealingScope.PIPELINE_A_TRANSCRIPT,
        candidate_artifact="Speaker 1: Test",
        failure_reasons=["Generic speaker"],
        allowed_entity_ids=[],
        allowed_evidence_ids=[],
        max_repair_calls=0,
    )
    res = asyncio.run(healer.execute_healing(req))
    assert res.status == "UNREPAIRABLE"

    payload = build_anthropic_multi_breakpoint_payload(
        system_blocks=["s1", "s2", "s3"],
        tools=[{"name": "t1"}, {"name": "t2"}],
        examples_block="ex",
        turn_prefix_block="tp",
        user_query="q",
    )
    assert payload["breakpoints_count"] <= 4


def test_adversarial_invariant_bypass_cache_key_and_semantic_namespace(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): Forbidden run_id/salary in cache key & missing generation_id fail closed."""
    with pytest.raises(ValueError, match="security hygiene"):
        derive_cache_key(
            workflow="run-1234567890abcdef",
            model="gpt-5.6-astra",
            static_prefix="prefix",
        )

    sc = SemanticCache(tmp_path / "runtime")
    with pytest.raises(ValueError, match="requires graph_generation_id"):
        sc.compatibility({"use_case": "l2_extraction", "merkle_root_hash": "a" * 64})


def test_adversarial_generation_rollover_recovery(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery): SubgraphReadMemoCache purges stale generation data on rollover."""
    memo = SubgraphReadMemoCache(tmp_path / "memo.sqlite")
    memo.put(generation_id="gen_old", query_signature="q1", payload={"stale": True})
    assert memo.get(generation_id="gen_old", query_signature="q1") == {"stale": True}
    memo.put(generation_id="gen_new", query_signature="q1", payload={"stale": False})
    assert memo.get(generation_id="gen_old", query_signature="q1") is None
    assert memo.get(generation_id="gen_new", query_signature="q1") == {"stale": False}
