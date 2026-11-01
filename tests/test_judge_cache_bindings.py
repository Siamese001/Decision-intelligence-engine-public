"""Unit tests for Wave 6 judge cache bindings, canonical keys, and usage normalization."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.eval_cache import (
    check_cache,
    check_pin,
    clear_evaluation_cache,
    get_judge_prompt_hash,
    update_evaluation_cache,
)
from dual_pipeline.transcript_qa_core.judge_cache_core import (
    CanonicalJudgeKey,
    NormalizedTokenUsage,
    SingleFlightExecutor,
)


def test_canonical_judge_key_valid():
    """Valid CanonicalJudgeKey produces deterministic serialization and hash."""
    key1 = CanonicalJudgeKey(
        artifact_type="output_1",
        content_sha256="a" * 64,
        reference_sha256="b" * 64,
        context_sha256="c" * 64,
        prompt_digest="d" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
        schema_version="2.0",
        security_partition="tenant-alpha",
    )
    key2 = CanonicalJudgeKey(
        artifact_type="output_1",
        content_sha256="a" * 64,
        reference_sha256="b" * 64,
        context_sha256="c" * 64,
        prompt_digest="d" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
        schema_version="2.0",
        security_partition="tenant-alpha",
    )
    valid, reason = key1.validate()
    assert valid is True
    assert reason == "Valid"
    assert key1.to_key() == key2.to_key()
    assert key1.to_key().startswith("judge:output_1:tenant-alpha:")


def test_canonical_judge_key_rejects_default_prompt():
    """Default prompt hash or empty digest is prohibited and rejected fail-closed."""
    key_default = CanonicalJudgeKey(
        artifact_type="output_2",
        content_sha256="a" * 64,
        reference_sha256="b" * 64,
        context_sha256="c" * 64,
        prompt_digest="default_prompt",
        model="gpt-6-astra",
        reasoning_effort="medium",
    )
    valid, reason = key_default.validate()
    assert valid is False
    assert "default prompt hash prohibited" in reason

    with pytest.raises(ValueError, match="default prompt hash prohibited"):
        key_default.to_key()


def test_canonical_judge_key_rejects_missing_dependencies():
    """Missing content or reference hashes fail validation."""
    key_no_content = CanonicalJudgeKey(
        artifact_type="output_3",
        content_sha256="",
        reference_sha256="b" * 64,
        context_sha256="c" * 64,
        prompt_digest="d" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
    )
    valid, _ = key_no_content.validate()
    assert valid is False

    key_no_ref = CanonicalJudgeKey(
        artifact_type="output_3",
        content_sha256="a" * 64,
        reference_sha256="",
        context_sha256="c" * 64,
        prompt_digest="d" * 16,
        model="gpt-6-astra",
        reasoning_effort="medium",
    )
    valid, _ = key_no_ref.validate()
    assert valid is False


def test_check_pin_rejects_hashless_and_mismatched_pins():
    """Pins lacking SHA or with mismatched SHA are strictly rejected."""
    expected = "e" * 64
    # Missing artifact_sha256
    hashless_pin = {
        "output_2": {
            "composite_score": 95,
            "verdict": "PASS",
        }
    }
    assert check_pin(hashless_pin, "output_2", "output_02", 90, expected) is None

    # Mismatched artifact_sha256
    mismatched_pin = {
        "output_2": {
            "artifact_sha256": "f" * 64,
            "composite_score": 95,
            "verdict": "PASS",
        }
    }
    assert check_pin(mismatched_pin, "output_2", "output_02", 90, expected) is None

    # Matching artifact_sha256
    valid_pin = {
        "output_2": {
            "artifact_sha256": expected,
            "composite_score": 95,
            "verdict": "PASS",
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 35,
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 30,
            },
        }
    }
    res = check_pin(valid_pin, "output_2", "output_02", 90, expected)
    assert res is not None
    assert res["source"] == "PINNED_CACHE"
    assert res["composite_score"] == 95


def test_eval_cache_stores_and_retrieves_failures():
    """Cache stores failed evaluations and returns them as reusable failures."""
    clear_evaluation_cache()
    key = "judge:test:fail_case"
    fail_eval: Dict[str, Any] = {
        "composite_score": 75,
        "verdict": "FAIL",
        "forensic_rationale": "Missing key stakeholder dynamics",
        "dimension_1": 20,
        "dimension_2": 20,
        "dimension_3": 20,
        "dimension_4": 15,
    }
    update_evaluation_cache(key, fail_eval, "", None, "", None)

    # Standard pass check ignores it
    assert check_cache(key, min_score=90) is None

    # allow_failure returns it without re-calling provider
    cached = check_cache(key, allow_failure=True)
    assert cached is not None
    assert cached["composite_score"] == 75
    assert cached["verdict"] == "FAIL"
    assert cached["source"] == "CONTENT_CACHE"


def test_normalized_token_usage_from_openai_shape():
    """Parses OpenAI-style usage with cached_tokens and reasoning_tokens."""
    raw = {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "prompt_tokens_details": {
            "cached_tokens": 800,
            "cache_creation_input_tokens": 0,
        },
        "completion_tokens_details": {
            "reasoning_tokens": 150,
        },
    }
    norm = NormalizedTokenUsage.from_raw(raw, model_id="gpt-6-astra", duration_seconds=1.2)
    assert norm.prompt_tokens == 1000
    assert norm.cached_tokens == 800
    assert norm.uncached_prompt_tokens == 200  # 1000 - 800
    assert norm.completion_tokens == 300
    assert norm.reasoning_tokens == 150
    assert norm.total_tokens == 1300
    assert norm.avoided_calls == 0


def test_normalized_token_usage_from_gemini_shape():
    """Parses Gemini-style usage with token counts."""
    raw = {
        "prompt_token_count": 2500,
        "candidates_token_count": 500,
        "cached_content_token_count": 2000,
    }
    norm = NormalizedTokenUsage.from_raw(raw, model_id="gemini-3.8-flash", duration_seconds=0.8)
    assert norm.prompt_tokens == 2500
    assert norm.cached_tokens == 2000
    assert norm.uncached_prompt_tokens == 500
    assert norm.completion_tokens == 500
    assert norm.total_tokens == 3000


def test_normalized_token_usage_result_cache_hit():
    """Result-cache hit records avoided calls and estimated saved tokens."""
    norm = NormalizedTokenUsage.from_raw(
        raw_usage=None,
        model_id="gpt-6-astra",
        is_result_cache_hit=True,
        estimated_tokens=1500,
    )
    assert norm.avoided_calls == 1
    assert norm.estimated_avoided_tokens == 1500
    assert norm.prompt_tokens == 0
    assert norm.completion_tokens == 0


def test_single_flight_deduplication():
    """Concurrent identical calls deduplicate into single invocation."""
    flight = SingleFlightExecutor()
    counter = 0

    def slow_eval():
        nonlocal counter
        time.sleep(0.05)
        counter += 1
        return {"score": 92}

    with ThreadPoolExecutor(max_workers=4) as executor:
        f1 = executor.submit(flight.execute, "key-shared", slow_eval)
        f2 = executor.submit(flight.execute, "key-shared", slow_eval)
        f3 = executor.submit(flight.execute, "key-shared", slow_eval)

        r1, dedup1 = f1.result()
        r2, dedup2 = f2.result()
        r3, dedup3 = f3.result()

    assert r1 == {"score": 92}
    assert r2 == {"score": 92}
    assert r3 == {"score": 92}
    # Exactly one executed slow_eval
    assert counter == 1
    # At least two were marked as deduplicated
    assert (dedup1 + dedup2 + dedup3) >= 2
