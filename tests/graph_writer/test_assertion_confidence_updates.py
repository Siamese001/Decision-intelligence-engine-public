"""
tests/graph_writer/test_assertion_confidence_updates.py

Verification of assertion confidence score calculations, Bayesian-style corroboration
updates, and label categorization in graph writer under AGENTS.md.
"""
from __future__ import annotations

import pytest

from dual_pipeline.graph_writer_core.assertions import (
    _resolve_confidence_label,
    _resolve_confidence_score,
)


def test_confidence_score_initial_resolution():
    """Verify confidence score defaults and candidate extraction score passthrough."""
    # Default fallback
    assert _resolve_confidence_score({}, {}, None) == 0.85

    # Extraction confidence in candidate
    cand = {"extraction_confidence": 0.72}
    assert _resolve_confidence_score({}, cand, None) == 0.72

    # Explicit score in conf_info overrides candidate
    conf_info = {"score": 0.95}
    assert _resolve_confidence_score(conf_info, cand, None) == 0.95


def test_confidence_score_corroboration_bump():
    """Verify incremental Bayesian-style corroboration updates from prior assertions."""
    # Prior confidence 0.80 -> 0.80 + 0.10 = 0.90
    cand = {"extraction_confidence": 0.85}
    score = _resolve_confidence_score({}, cand, prior_confidence=0.80)
    assert pytest.approx(score, rel=1e-3) == 0.90

    # Prior confidence 0.90 -> 0.90 + 0.05 = 0.95
    score_2 = _resolve_confidence_score({}, cand, prior_confidence=0.90)
    assert pytest.approx(score_2, rel=1e-3) == 0.95

    # Cap at 0.99
    score_cap = _resolve_confidence_score({}, cand, prior_confidence=0.985)
    assert score_cap == 0.99


def test_confidence_label_mapping():
    """Verify label categorization from score thresholds and explicit valid tiers."""
    # Score-based labels
    assert _resolve_confidence_label({}, 0.85) == "high"
    assert _resolve_confidence_label({}, 0.70) == "medium"
    assert _resolve_confidence_label({}, 0.50) == "low"

    # Explicit tier in conf_info
    assert _resolve_confidence_label({"tier": "CONFIRMED"}, 0.50) == "confirmed"
    assert _resolve_confidence_label({"tier": "directional"}, 0.90) == "directional"
    assert _resolve_confidence_label({"tier": "superseded"}, 0.99) == "superseded"

    # Invalid tier defaults to directional
    assert _resolve_confidence_label({"tier": "invalid_tier"}, 0.95) == "directional"
