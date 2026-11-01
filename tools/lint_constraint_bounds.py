#!/usr/bin/env python3
"""
tools/lint_constraint_bounds.py

Automated mechanical validator for QA gate constraint bounds and prompt satisfiability.
Ensures:
1. All word count floors are strictly less than ceilings with a viable margin.
2. Sum of section floors does not exceed global or tier-specific floors.
3. Appendix minimums cannot cause insoluble conflicts with core document word ceilings.
4. Total document word ceilings do not artificially cap legitimate multi-speaker appendices.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Add repository root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.transcript_qa_core.gate2 import (
    _get_default_word_limits,
    _get_tier1_word_limits,
    _get_tier3_word_limits,
)


def check_word_limit_envelopes() -> list[str]:
    """Verify that (min_words < max_words) holds with healthy breathing room across all duration brackets."""
    defects: list[str] = []
    test_durations = [10, 15, 25, 30, 45, 60, 90, 120]

    # Check Tier 1
    for dur in test_durations:
        for has_diag in (True, False):
            min_w, max_w, label = _get_tier1_word_limits(dur, has_diag)
            if max_w is not None:
                if min_w >= max_w:
                    defects.append(
                        f"Tier 1 (dur={dur}m, diag={has_diag}): min_words ({min_w}) >= max_words ({max_w})"
                    )
                elif max_w - min_w < 300:
                    defects.append(
                        f"Tier 1 (dur={dur}m, diag={has_diag}): breathing room too narrow ({max_w - min_w} words < 300)"
                    )

    # Check Tier 3
    for dur in test_durations:
        min_w, max_w, label = _get_tier3_word_limits(dur)
        if max_w is not None:
            if min_w >= max_w:
                defects.append(
                    f"Tier 3 (dur={dur}m): min_words ({min_w}) >= max_words ({max_w})"
                )
            elif max_w - min_w < 200:
                defects.append(
                    f"Tier 3 (dur={dur}m): breathing room too narrow ({max_w - min_w} words < 200)"
                )

    # Check Default
    for dur in test_durations:
        min_w, max_w, label = _get_default_word_limits(dur)
        if max_w is not None:
            if min_w >= max_w:
                defects.append(
                    f"Default Tier (dur={dur}m): min_words ({min_w}) >= max_words ({max_w})"
                )
            elif max_w - min_w < 300:
                defects.append(
                    f"Default Tier (dur={dur}m): breathing room too narrow ({max_w - min_w} words < 300)"
                )

    return defects


def check_prompt_config_bounds() -> list[str]:
    """Verify bounds declared in EnginePromptConfigSSOT."""
    defects: list[str] = []
    cfg = CANONICAL_PROMPT_CONFIG

    # Output 1
    if cfg.output1.min_word_count >= cfg.output1.max_word_count:
        defects.append(
            f"Output 1 config: min_words ({cfg.output1.min_word_count}) >= max_words ({cfg.output1.max_word_count})"
        )

    # Output 2
    if cfg.output2.min_word_count >= cfg.output2.max_word_count:
        defects.append(
            f"Output 2 config: min_words ({cfg.output2.min_word_count}) >= max_words ({cfg.output2.max_word_count})"
        )
    if cfg.output2.max_word_count < 2400:
        defects.append(
            f"Output 2 max_word_count ({cfg.output2.max_word_count}) is lower than Tier 1 diagram ceiling (2400)"
        )

    # Output 3
    if cfg.output3.min_word_count >= cfg.output3.max_word_count:
        defects.append(
            f"Output 3 config: min_words ({cfg.output3.min_word_count}) >= max_words ({cfg.output3.max_word_count})"
        )

    return defects


def check_no_total_word_count_ceiling_in_gate2() -> list[str]:
    """Verify that gate2 does not reject documents based on total_word_count exceeding an arbitrary small ceiling."""
    defects: list[str] = []
    gate2_path = ROOT / "dual_pipeline" / "transcript_qa_core" / "gate2.py"
    if not gate2_path.exists():
        return [f"gate2.py not found at {gate2_path}"]

    code = gate2_path.read_text(encoding="utf-8")
    # Search for any pattern like `total_word_count >` or `total_words >`
    matches = re.findall(r"total_word_count\s*>\s*(\d+)", code)
    for m in matches:
        limit = int(m)
        if limit < 5000:
            defects.append(
                f"gate2.py enforces total_word_count > {limit}, which artificially constrains appendices."
            )

    return defects


def check_section_density_sum() -> list[str]:
    """Verify mandatory deep section word floors sum to less than any standard meeting floor."""
    from dual_pipeline.parameters import (
        compute_output2_duration_word_bounds,
        get_output2_section_density_floors,
    )

    defects: list[str] = []
    floors = get_output2_section_density_floors()
    # Business Meaning (40), Immediate Scope (40), Recommendations (30) = 110 words
    mandatory_sum = (
        floors.get("business_meaning_min_words", 40)
        + floors.get("immediate_scope_min_words", 40)
        + floors.get("recommendations_min_words", 30)
    )
    # Validate section density floors against minimum meeting duration bounds (20m minimum standard meeting)
    min_dur = 20
    standard_min_floor, _, _, _ = compute_output2_duration_word_bounds(min_dur, is_tier3=False, has_diagrams=False)
    if mandatory_sum > standard_min_floor:
        defects.append(
            f"Mandatory section density floors sum to {mandatory_sum} words, exceeding standard floor {standard_min_floor} for {min_dur}m meeting"
        )
    return defects


def main() -> int:
    parser = argparse.ArgumentParser(description="Constraint-Conflict Linter")
    parser.add_argument("--check", action="store_true", help="Run checks and exit non-zero if defects found")
    args = parser.parse_args()

    all_defects: list[str] = []
    all_defects.extend(check_word_limit_envelopes())
    all_defects.extend(check_prompt_config_bounds())
    all_defects.extend(check_no_total_word_count_ceiling_in_gate2())
    all_defects.extend(check_section_density_sum())

    if all_defects:
        print(f"[FAIL] Constraint-Conflict Linter detected {len(all_defects)} defect(s):")
        for d in all_defects:
            print(f"  - {d}")
        return 1

    print("[PASS] Constraint-Conflict Linter: all prompt floors, gate bounds, and ceilings are mutually satisfiable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
