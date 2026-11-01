"""Unit tests for Google Gemini Flash post-runtime query expansion provider and cosine filter."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from dual_pipeline.l1_semantic_evaluator import L1SemanticSufficiencyEvaluator
from dual_pipeline.query_expansion_google import (
    GoogleQueryExpansionProvider,
    compute_deterministic_vector,
    cosine_similarity,
)


def test_cosine_similarity_basic():
    """Verify basic cosine similarity calculations."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2)) == 1.0

    v_orth = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v_orth)) == 0.0

    v_opp = [-1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v_opp)) == -1.0

    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_compute_deterministic_vector():
    """Verify normalized deterministic vector generation."""
    v1 = compute_deterministic_vector("APEX Land Management")
    v2 = compute_deterministic_vector("APEX Land Management")
    assert v1 == v2
    assert len(v1) == 128

    # Norm should be approximately 1.0
    norm = sum(x * x for x in v1) ** 0.5
    assert pytest.approx(norm, rel=1e-3) == 1.0


def test_google_query_expansion_provider_basic():
    """Verify query expansion returns variants filtered by cosine similarity threshold."""
    provider = GoogleQueryExpansionProvider(similarity_threshold=0.80)

    simulated_json = json.dumps({
        "expansions": [
            "What is APEX's land management operational workflow?",  # Highly relevant
            "APEX land management operations and boundaries",       # Highly relevant
            "Completely unrelated topic about cooking recipes",    # Low relevance (should be filtered)
        ]
    })

    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        return_value=simulated_json,
    ):
        results = provider.expand_query("APEX land management operational workflow", count=3)
        assert len(results) >= 1
        assert "What is APEX's land management operational workflow?" in results
        assert "Completely unrelated topic about cooking recipes" not in results
        assert provider.last_ttft_ms >= 0.0


def test_google_query_expansion_empty_query():
    """Verify empty query returns empty list without calling API."""
    provider = GoogleQueryExpansionProvider()
    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content") as mock_api:
        assert provider.expand_query("   ") == []
        assert mock_api.call_count == 0


def test_google_query_expansion_api_failure():
    """Verify provider exception returns empty list gracefully."""
    provider = GoogleQueryExpansionProvider()
    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        side_effect=RuntimeError("Google API 500 error"),
    ):
        assert provider.expand_query("Valid Query") == []


def test_l1_semantic_evaluator_integration():
    """Verify L1SemanticSufficiencyEvaluator uses query_expansion_provider when available."""
    provider = GoogleQueryExpansionProvider(similarity_threshold=0.50)

    simulated_json = json.dumps({
        "expansions": [
            "Detailed RACI model for PartnerAdvisory NEA co-delivery",
            "PartnerAdvisory and NEA engineering pod ownership guidelines",
        ]
    })

    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        return_value=simulated_json,
    ):
        evaluator = L1SemanticSufficiencyEvaluator(
            mock_mode=True,
            query_expansion_provider=provider,
        )

        plan = {
            "query_type": "relationship_explanation",
            "subquestions": ["How is RACI defined between PartnerAdvisory and NEA?"],
        }
        # Answer with missing coverage
        answer = {"rationale": "General meeting discussion."}
        sealed_l2 = {"result": "ok"}

        result = evaluator.evaluate_sufficiency(
            question="How is RACI defined between PartnerAdvisory and NEA?",
            plan=plan,
            answer=answer,
            sealed_l2=sealed_l2,
        )

        assert result.is_sufficient is False
        assert result.replan_directive is not None
        expansions = result.replan_directive["suggested_query_expansion"]
        assert len(expansions) >= 1
        assert any("RACI" in exp for exp in expansions)
