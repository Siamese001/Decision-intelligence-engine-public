"""
tests/test_output2_antipattern_checks.py

Targeted tests verifying all 7 Output 02 antipattern fixes are mechanically
enforced by the new deterministic checks and prompt contract changes.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from dual_pipeline.llm_judge_core.recap_checks import (
    PASSIVE_HEDGING_PATTERNS,
    ROLE_ANONYMIZATION_PATTERNS,
    check_name_anonymization_defect,
    check_passive_hedging_defect,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
PROMPT_CONTRACT = Path("config/prompts/synthesis/stages/02_executive_recap.md")


@pytest.fixture()
def prompt_text() -> str:
    return PROMPT_CONTRACT.read_text(encoding="utf-8")


_CLEAN_RECAP = """\
# Subject: Project LENS Status -- Executive Strategic & Technical Brief | 09/18/2026

## Meeting Objective
The team reviewed architecture v1.2 and STP threshold relaxation.

## Key Discussion Points & Findings
- **Jake proposed relaxing the STP threshold from 95% to 65%–84%.** Jake explained the rationale.
- **Amit challenged single-turbine data extrapolation.** Amit argued one turbine cannot generalize.
- **Miles proposed lightweight image quality checks.** Miles described Laplacian variance approach.
"""

_ANONYMIZED_RECAP = """\
# Subject: Project LENS Status

## Key Discussion Points & Findings
- **The UI lead identified a late clarification.** The UI lead described the problem.
- **The lead architect favored recalibration.** The lead architect pushed back.
- **The technical contributor discussed failure modes.** The technical contributor noted issues.
- **The UI lead explained inference paths.** The UI lead walked through the zones.
"""

_HEDGING_RECAP = """\
# Subject: Project LENS Status

## Key Discussion Points & Findings
- Jake did not claim certainty about whether items should enter the accuracy calculation.
- The approach remained uncertain, making the metric an unresolved issue.
- The team discussed without confirming the exact model versions.
- This was not an agreed collection target.
- It was not a demonstrated change during the meeting.
- No implementation commitment followed after the proposal.
- His contribution therefore separated known behavior from the unresolved question.
"""


# ---------------------------------------------------------------------------
# AP-2: Name anonymization defect check
# ---------------------------------------------------------------------------
class TestNameAnonymizationDefect:
    """Tests for check_name_anonymization_defect (AP-2)."""

    def test_clean_recap_passes(self) -> None:
        result = check_name_anonymization_defect(_CLEAN_RECAP)
        assert result is None, f"Expected None for clean recap, got: {result}"

    def test_anonymized_recap_fails(self) -> None:
        result = check_name_anonymization_defect(_ANONYMIZED_RECAP)
        assert result is not None, "Should detect role-based anonymization"
        assert "role-based anonymization" in result.lower()

    def test_threshold_below_3_passes(self) -> None:
        text = "## Key Discussion Points\n- **The UI lead** said something.\n- **The UI lead** repeated."
        result = check_name_anonymization_defect(text)
        assert result is None, "2 matches should be below threshold of 3"

    def test_pattern_coverage(self) -> None:
        """All canonical role patterns should be detected."""
        for role in ["the UI lead", "the lead architect", "the technical contributor",
                     "the senior architect", "the primary speaker"]:
            assert ROLE_ANONYMIZATION_PATTERNS.search(role), f"Pattern should match: {role}"

    def test_names_not_flagged(self) -> None:
        """Actual names should not trigger the check."""
        for name in ["Jake", "Amit", "Miles"]:
            assert ROLE_ANONYMIZATION_PATTERNS.search(name) is None


# ---------------------------------------------------------------------------
# AP-3: Passive hedging defect check
# ---------------------------------------------------------------------------
class TestPassiveHedgingDefect:
    """Tests for check_passive_hedging_defect (AP-3)."""

    def test_clean_recap_passes(self) -> None:
        result = check_passive_hedging_defect(_CLEAN_RECAP)
        assert result is None, f"Expected None for clean recap, got: {result}"

    def test_hedging_recap_fails(self) -> None:
        result = check_passive_hedging_defect(_HEDGING_RECAP)
        assert result is not None, "Should detect excessive passive hedging"
        assert "passive-aggressive cop-out" in result.lower()

    def test_threshold_boundary_passes(self) -> None:
        """Exactly 5 hedges should pass (threshold is >5)."""
        text = "\n".join(PASSIVE_HEDGING_PATTERNS[:5])
        result = check_passive_hedging_defect(text)
        assert result is None, f"5 matches at threshold should pass, got: {result}"

    def test_threshold_boundary_fails(self) -> None:
        """6 hedges should fail."""
        text = "\n".join(PASSIVE_HEDGING_PATTERNS[:6])
        result = check_passive_hedging_defect(text)
        assert result is not None, "6 matches should exceed threshold"


# ---------------------------------------------------------------------------
# AP-2 Prompt Contract: Name-neutral rule removed
# ---------------------------------------------------------------------------
class TestPromptContractFixes:
    """Verify prompt contract no longer contains defective patterns."""

    def test_no_name_neutral_rule(self, prompt_text: str) -> None:
        """AP-2: Prompt must not instruct 'by role, not by name'."""
        assert "referencing speakers by role, not by name" not in prompt_text
        assert "name-neutral intrinsic exemplar" not in prompt_text

    def test_direct_attribution_mandate(self, prompt_text: str) -> None:
        """AP-2: Prompt must mandate direct personal attribution."""
        assert "direct personal attribution" in prompt_text

    def test_banned_passive_qualifiers(self, prompt_text: str) -> None:
        """AP-3: Prompt must list banned passive qualifiers."""
        assert "did not claim" in prompt_text
        assert "Strictly Banned Passive Qualifiers" in prompt_text

    def test_exemplar_uses_names(self, prompt_text: str) -> None:
        """AP-2/AP-5: Exemplar must use direct names, not role placeholders."""
        assert "Jake proposed" in prompt_text or "Jake explained" in prompt_text
        assert "Amit" in prompt_text
        # Old defective patterns must be gone
        assert "[PRIMARY_SPEAKER]" not in prompt_text
        assert "[SENIOR_ARCHITECT]" not in prompt_text

    def test_anchor_format_mandate(self, prompt_text: str) -> None:
        """AP-5: Prompt must mandate Actor Name in bold anchors."""
        assert "Bold anchors MUST name the actor" in prompt_text


# ---------------------------------------------------------------------------
# AP-6: Judge collateral binding
# ---------------------------------------------------------------------------
class TestJudgeCollateralBinding:
    """Verify _run_llm_inference accepts collateral_text parameter."""

    def test_signature_accepts_collateral(self) -> None:
        from dual_pipeline.llm_judge_core.recap import _run_llm_inference
        sig = inspect.signature(_run_llm_inference)
        assert "collateral_text" in sig.parameters, (
            "_run_llm_inference must accept collateral_text parameter"
        )
        param = sig.parameters["collateral_text"]
        assert param.default == "", "collateral_text should default to empty string"
