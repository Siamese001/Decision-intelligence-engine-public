"""End-to-end integration tests for Wave 6 judge evaluation caching and prefix stability."""
from __future__ import annotations

import hashlib
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.judges.recap import build_recap_judge_user_content
from dual_pipeline.transcript_qa_core.eval_cache import (
    clear_evaluation_cache,
    resolve_pins_and_cache,
    update_evaluation_cache,
)


def test_end_to_end_identical_request_zero_provider_calls():
    """Identical evaluation requests retrieve cached evaluations with zero fresh model calls."""
    clear_evaluation_cache()

    f1 = "Alice: Welcome to the meeting.\nBob: Thanks Alice."
    f2 = "# Executive Summary\nStrategic kickoff meeting."
    f3 = '{"entities": [{"id": "person:alice"}], "decisions": []}'
    src = "Alice: Welcome to the meeting.\nBob: Thanks Alice."
    model = "gpt-6-astra"
    effort = "medium"

    # Step 1: Initial resolution yields misses
    o1, o2, o3, k1, k2, k3 = resolve_pins_and_cache(
        f1, f2, f3, model, effort, pinned={}, source_text=src
    )
    assert o1 is None
    assert o2 is None
    assert o3 is None

    # Step 2: Populate cache as if judges completed successfully
    dim1 = 95 / 4.0
    e1 = {"composite_score": 95, "verdict": "PASS", "evaluator": "Forensic", "dimension_1": dim1, "dimension_2": dim1, "dimension_3": dim1, "dimension_4": dim1}
    sec2_92 = {"header_and_overview": 10.0, "key_updates_technical_substance": 32.0, "conditional_extensions": 18.0, "appendix_stakeholder_perspectives": 32.0}
    e2 = {"composite_score": 92, "verdict": "PASS", "evaluator": "Recap", "section_scores": sec2_92}
    e3 = {"composite_score": 94, "verdict": "PASS", "evaluator": "Graph", "dimension_1": 14, "dimension_2": 14, "dimension_3": 19, "dimension_4": 14, "dimension_5": 14, "dimension_6": 19}
    update_evaluation_cache(k1, e1, k2, e2, k3, e3)

    # Step 3: Second call with identical inputs hits content cache
    o1_cached, o2_cached, o3_cached, k1_b, k2_b, k3_b = resolve_pins_and_cache(
        f1, f2, f3, model, effort, pinned={}, source_text=src
    )
    assert k1_b == k1
    assert k2_b == k2
    assert k3_b == k3

    assert o1_cached is not None
    assert o1_cached["source"] == "CONTENT_CACHE"
    assert o1_cached["composite_score"] == 95

    assert o2_cached is not None
    assert o2_cached["source"] == "CONTENT_CACHE"
    assert o2_cached["composite_score"] == 92

    assert o3_cached is not None
    assert o3_cached["source"] == "CONTENT_CACHE"
    assert o3_cached["composite_score"] == 94


def test_end_to_end_single_component_miss_evaluates_only_affected():
    """Mutating only Output 2 preserves cached hits for Output 1 and Output 3."""
    clear_evaluation_cache()

    f1 = "Alice: Stable transcript text."
    f2 = "# Executive Summary\nFirst attempt at recap."
    f3 = '{"entities": [{"id": "person:alice"}]}'
    src = "Alice: Stable transcript text."
    model = "gpt-6-astra"
    effort = "medium"

    _, _, _, k1, k2, k3 = resolve_pins_and_cache(f1, f2, f3, model, effort, {}, source_text=src)
    dim1 = 95 / 4.0
    sec2_82 = {"header_and_overview": 8.0, "key_updates_technical_substance": 30.0, "conditional_extensions": 14.0, "appendix_stakeholder_perspectives": 30.0}
    update_evaluation_cache(
        k1, {"composite_score": 95, "verdict": "PASS", "dimension_1": dim1, "dimension_2": dim1, "dimension_3": dim1, "dimension_4": dim1},
        k2, {"composite_score": 82, "verdict": "FAIL", "section_scores": sec2_82},
        k3, {"composite_score": 94, "verdict": "PASS", "dimension_1": 14, "dimension_2": 14, "dimension_3": 19, "dimension_4": 14, "dimension_5": 14, "dimension_6": 19},
    )

    # Now mutate Output 2 only
    f2_repaired = "# Executive Summary\nSecond repaired attempt at recap."
    o1, o2, o3, _, k2_new, _ = resolve_pins_and_cache(f1, f2_repaired, f3, model, effort, {}, source_text=src)

    # Output 1 and 3 hit cache
    assert o1 is not None and o1["source"] == "CONTENT_CACHE"
    assert o3 is not None and o3["source"] == "CONTENT_CACHE"

    # Output 2 is a cache miss, requiring only Output 2 to run
    assert o2 is None
    assert k2_new != k2


def test_end_to_end_source_text_mutation_invalidates_all_caches():
    """Mutating the underlying meeting source text invalidates all output caches."""
    clear_evaluation_cache()

    f1 = "Alice: Transcript."
    f2 = "# Executive Summary"
    f3 = '{"entities": []}'
    src_v1 = "Meeting source audio transcript v1"
    src_v2 = "Meeting source audio transcript v2 (corrected diarization)"
    model = "gpt-6-astra"
    effort = "medium"

    _, _, _, k1, k2, k3 = resolve_pins_and_cache(f1, f2, f3, model, effort, {}, source_text=src_v1)
    dim1 = 95 / 4.0
    sec2_92 = {"header_and_overview": 10.0, "key_updates_technical_substance": 32.0, "conditional_extensions": 18.0, "appendix_stakeholder_perspectives": 32.0}
    update_evaluation_cache(
        k1, {"composite_score": 95, "verdict": "PASS", "dimension_1": dim1, "dimension_2": dim1, "dimension_3": dim1, "dimension_4": dim1},
        k2, {"composite_score": 92, "verdict": "PASS", "section_scores": sec2_92},
        k3, {"composite_score": 94, "verdict": "PASS", "dimension_1": 14, "dimension_2": 14, "dimension_3": 19, "dimension_4": 14, "dimension_5": 14, "dimension_6": 19},
    )

    # When source text changes to v2, all outputs miss cache because dependency hash changed
    o1, o2, o3, _, _, _ = resolve_pins_and_cache(f1, f2, f3, model, effort, {}, source_text=src_v2)
    assert o1 is None
    assert o2 is None
    assert o3 is None


def test_end_to_end_prompt_prefix_stability_across_recap_and_collateral():
    """Recap prompt prefix including governance context and collateral remains invariant across candidate retries."""
    gov_ctx = "Authoritative strategic governance context for recap."
    ref_text = "Alice: Ground truth transcript with multi-speaker dialogue."
    collateral = "Q3 Roadmap Slide 4: Budget allocation details."

    c1 = "# Executive Summary\nInitial draft."
    c2 = "# Executive Summary\nSurgically repaired draft."

    p1 = build_recap_judge_user_content(gov_ctx, ref_text, c1, collateral_block=collateral)
    p2 = build_recap_judge_user_content(gov_ctx, ref_text, c2, collateral_block=collateral)

    # Collateral and reference transcript must strictly precede candidate output
    col_idx_1 = p1.index("Q3 Roadmap Slide 4")
    ref_idx_1 = p1.index("<reference_forensic_transcript>")
    cand_idx_1 = p1.index("<candidate_output_2>")

    assert col_idx_1 < cand_idx_1
    assert ref_idx_1 < cand_idx_1

    # Entire prefix up to <candidate_output_2> must be 100% byte-identical
    cand_tag = "<candidate_output_2>"
    prefix_1 = p1[:p1.index(cand_tag)]
    prefix_2 = p2[:p2.index(cand_tag)]
    assert prefix_1 == prefix_2
    assert len(prefix_1) > 100
