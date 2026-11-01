#!/usr/bin/env python3
"""Unit and boundary tests for scope-scaled wave plan word budgets and HITL exemptions."""

from __future__ import annotations

from pathlib import Path

from tools.implementation_plan_core.evidence_requirements import (
    compute_max_plan_words,
    strip_hitl_decision_blocks,
)
from tools.validate_implementation_plan import validate_file, validate_plan_content

ROOT = Path(__file__).resolve().parents[1]


def _make_wave_block(wave_num: int, num_items: int = 2, extra_words: int = 0) -> str:
    items = "\n".join(f"- `[MODIFY]` `module_{wave_num}_{i}.py`: Update contract." for i in range(num_items))
    filler = " ".join(f"specword{i}" for i in range(extra_words))
    return f"""## Wave {wave_num}: Feature Wave {wave_num}

### Objective & Scope
Implement bounded wave {wave_num} behavior. {filler}

### Milestones & Deliverables
{items}

### Acceptance Criteria
- Wave {wave_num} behavior passes with representative inputs.

### Runtime Receipt & Completion Gate
- **Run**: `.venv/bin/python tools/run_tests_with_progress.py --timeout=60 tests/test_wave_{wave_num}.py`
- **Input**: Representative fixture for wave {wave_num}.
- **Expected**: 100% pass and verified state.
- **Verify independently**: Inspect JSON receipt and state output.
- **Fail if**: Any check returns default fallback.
- **Record**: `reports/wave{wave_num}_receipt.json`
"""


def test_single_wave_plan_retains_1000_word_floor_and_fails_when_bloated() -> None:
    """A 1-wave plan with 2 deliverables is capped at 1,000 words and fails at ~1,150 words."""
    bloated_1wave = "# Bloated Single Wave Plan\n\n" + _make_wave_block(1, num_items=2, extra_words=980)
    is_valid, errors = validate_plan_content(bloated_1wave, "bloated_1wave.md", strict=True)
    assert not is_valid
    assert any("capped at 1000 words" in e for e in errors), f"Unexpected errors: {errors}"


def test_five_wave_complex_plan_scales_budget_and_passes() -> None:
    """A 5-wave plan with 20 deliverables scales to 400 + 250*5 + 25*20 = 2,150 words and passes at ~1,800 words."""
    waves_md = "\n".join(_make_wave_block(w, num_items=4, extra_words=250) for w in range(1, 6))
    complex_5wave = "# Five-Wave Complex Plan\n\n" + waves_md
    word_count = len(complex_5wave.split())
    assert 1600 <= word_count <= 2100, f"Expected ~1,800 words, got {word_count}"
    is_valid, errors = validate_plan_content(complex_5wave, "complex_5wave.md", strict=True)
    assert is_valid, f"Expected 5-wave plan ({word_count} words) to pass, got: {errors}"


def test_monolithic_plan_exceeding_2500_ceiling_fails_closed() -> None:
    """Even a 5-wave plan with many deliverables cannot exceed the 2,500-word hard ceiling."""
    waves_md = "\n".join(_make_wave_block(w, num_items=8, extra_words=450) for w in range(1, 6))
    monolithic = "# Over-Ceiling Plan\n\n" + waves_md
    is_valid, errors = validate_plan_content(monolithic, "monolithic.md", strict=True)
    assert not is_valid
    assert any("capped at 2500 words" in e for e in errors), f"Unexpected errors: {errors}"


def test_hitl_decision_tables_and_json_blocks_are_exempt_from_word_count() -> None:
    """HITL decision tables and deferred-decisions JSON blocks do not trigger word-limit failures."""
    base_plan = "# Plan With HITL Disclosures\n\n" + _make_wave_block(1, num_items=2, extra_words=820)
    # Without HITL block, base_plan is ~920 words (passes 1,000 cap).
    is_valid_base, errors_base = validate_plan_content(base_plan, "base.md", strict=True)
    assert is_valid_base, f"Base plan failed: {errors_base}"

    hitl_block = """
| ID | Decision | Options (confidence) | Delta | Handling |
| :--- | :--- | :--- | :--- | :--- |
| DEC-01 | Choose storage mode | Option A (90%) vs Option B (40%) with detailed trade-off rationale and provenance notes across fifty words of explanation so that operators have full visibility into architectural consequences | 50% | Autonomous |

```json:deferred-decisions
[
  {
    "decision_id": "DEC-01",
    "wave": 1,
    "status": "RESOLVED",
    "question": "Which storage mode should be used for isolated evaluation runs?",
    "options": [
      {"label": "A", "plain_english_summary": "Option A summary words", "confidence_score": 0.90}
    ]
  }
]
```
"""
    combined = base_plan + "\n" + hitl_block
    assert len(combined.split()) > 1000
    assert len(strip_hitl_decision_blocks(combined).split()) < 1000
    is_valid_combined, errors_combined = validate_plan_content(combined, "combined.md", strict=True)
    assert is_valid_combined, f"HITL-augmented plan should pass, got: {errors_combined}"


def test_uncompressed_evals_reference_plan_validates_cleanly() -> None:
    """Verify the ~1,820-word 5-wave Evals Harness Uncompressed Reference Plan passes strict validation."""
    ref_plan = ROOT / "documentation" / "EVALS_HARNESS_UNCOMPRESSED_REFERENCE_PLAN.md"
    if ref_plan.is_file():
        is_valid, errors = validate_file(ref_plan, strict=True)
        assert is_valid, f"Uncompressed 5-wave reference plan failed validation: {errors}"
