"""
Tests for tools/lint_constraint_bounds.py.
Verifies that the constraint-conflict linter correctly validates current bounds and catches artificial conflicts.
"""

from unittest.mock import patch

from tools.lint_constraint_bounds import (
    check_no_total_word_count_ceiling_in_gate2,
    check_prompt_config_bounds,
    check_section_density_sum,
    check_word_limit_envelopes,
)


def test_current_constraints_pass():
    """Verify that current codebase has zero constraint conflicts."""
    assert check_word_limit_envelopes() == []
    assert check_prompt_config_bounds() == []
    assert check_no_total_word_count_ceiling_in_gate2() == []
    assert check_section_density_sum() == []


def test_detects_narrow_or_inverted_word_limits():
    """Verify that inverted min/max word limits are flagged."""
    with patch("tools.lint_constraint_bounds._get_tier1_word_limits", return_value=(1500, 1200, "label")):
        defects = check_word_limit_envelopes()
        assert len(defects) > 0
        assert any("min_words (1500) >= max_words (1200)" in d for d in defects)


def test_detects_narrow_breathing_room():
    """Verify that word limits with breathing room < 300 are flagged."""
    with patch("tools.lint_constraint_bounds._get_tier1_word_limits", return_value=(800, 950, "label")):
        defects = check_word_limit_envelopes()
        assert len(defects) > 0
        assert any("breathing room too narrow (150 words < 300)" in d for d in defects)
