"""Adversarial and edge-case tests for centralized SSOT configuration and AST drift linters.

Covers mandatory edge-case categories:
1. Boundary payloads (0-minute, negative, float, and marathon 10,000-minute durations)
2. Malformed input (corrupted JSON, wrong schema types, missing parameter subtrees)
3. Concurrency & race (16-worker concurrent ParameterRegistry reads and compare-and-swap updates)
4. Invariant bypass attempts (AST linter detection of rogue local word-limit functions and duplicate tables)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dual_pipeline.parameters import (
    ParameterRegistry,
    get_output2_c21_speaker_tier_floors,
    get_output2_section_density_floors,
    get_output2_word_count_limits,
)
from tools.lint_hardcoded_parameters import scan_file as scan_hardcoded_file
from tools.scan_ssot_configuration_violations import scan_source_code_violations


def test_boundary_duration_payloads_never_invert_floors_and_ceilings() -> None:
    """Category 1 (Boundary payload): Zero, negative, boundary, and extreme durations maintain min < max."""
    boundary_durations = [-10, 0, 1, 10, 14, 15, 29, 30, 44, 45, 59, 60, 89, 90, 150, 151, 10000]
    for dur in boundary_durations:
        for tier in (None, "TIER_1_DEEP_ARCHITECTURE", "TIER_2_STANDARD", "TIER_3_LIGHTWEIGHT_STATUS"):
            for has_diag in (False, True):
                min_w, max_w, label = get_output2_word_count_limits(dur, has_diagrams=has_diag, rigor_tier=tier)
                assert min_w > 0
                if max_w is not None:
                    assert max_w > min_w
                assert isinstance(label, str)


def test_malformed_parameters_file_fails_fast_and_wrong_types_fallback(tmp_path: Path, monkeypatch) -> None:
    """Category 2 (Malformed input): Corrupted JSON fails fast; wrong field types fall back safely to defaults."""
    malformed_path = tmp_path / "corrupt_parameters.json"
    malformed_path.write_text("{corrupted_json: [1, 2, ", encoding="utf-8")

    corrupt_reg = ParameterRegistry(config_path=malformed_path)
    with pytest.raises(ValueError, match="Fail-Fast: Invalid JSON"):
        corrupt_reg.get_int("grounding.delivery_max_words", 500)

    wrong_types_path = tmp_path / "wrong_types_parameters.json"
    wrong_types_path.write_text(
        '{"parameters": {"output2": {"section_density_floors": {"business_meaning_min_words": "not_an_int"}}}}',
        encoding="utf-8",
    )
    wrong_reg = ParameterRegistry(config_path=wrong_types_path)
    monkeypatch.setattr("dual_pipeline.parameters._GLOBAL_REGISTRY", wrong_reg)

    floors = get_output2_section_density_floors()
    assert floors["business_meaning_min_words"] == 40
    assert floors["standard_30m_section_floor"] == 60

    c21 = get_output2_c21_speaker_tier_floors()
    assert c21["primary"] == (150, 3)
    assert c21["co_lead"] == (80, 2)


def test_concurrent_parameter_reads_and_cas_mutations(tmp_path: Path) -> None:
    """Category 3 (Concurrency & race): 16 threads performing simultaneous reads and CAS updates."""
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "config" / "runtime" / "parameters.json"
    isolated_cfg = tmp_path / "parameters.json"
    isolated_cfg.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    reg = ParameterRegistry(config_path=isolated_cfg)

    def worker(worker_id: int) -> bool:
        for i in range(10):
            val = reg.get_int("grounding.delivery_max_words", 500)
            assert val >= 500
            if i == 0:
                try:
                    reg.compare_and_swap(
                        "grounding.delivery_max_words",
                        expected_current_value=val,
                        new_value=val + 1,
                        persist=False,
                    )
                except ValueError:
                    # Expected under concurrent CAS contention
                    pass
        return True

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(worker, range(16)))

    assert all(results)
    assert reg.get_int("grounding.delivery_max_words", 500) > 500


def test_invariant_bypass_detected_by_ast_linters(tmp_path: Path) -> None:
    """Category 4 (Invariant bypass attempt): Rogue local threshold tables or word-limit functions fail closed."""
    rogue_module = tmp_path / "dual_pipeline" / "rogue_evaluator.py"
    rogue_module.parent.mkdir(parents=True, exist_ok=True)
    rogue_module.write_text(
        '''"""Rogue evaluator attempting to redefine SSOT tables."""
_TIER1_THRESHOLDS = [(15, 100, 500, "rogue")]
WORD_SALAD_PATTERNS = ("rogue pattern",)

def _get_tier1_word_limits(dur: int):
    return 100, 500, "rogue"
''',
        encoding="utf-8",
    )

    ast_violations = scan_hardcoded_file(rogue_module, tmp_path)
    rules = {v.rule for v in ast_violations}
    assert "DUPLICATE_SSOT_CONSTANT" in rules
    assert "LOCAL_WORD_LIMIT_FUNCTION" in rules

    scanner_violations = scan_source_code_violations(files=[str(rogue_module)])
    assert len(scanner_violations) >= 2
