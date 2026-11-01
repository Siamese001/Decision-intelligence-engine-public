"""Unit tests for the AST configuration discovery scanner in tools/generate_ssot_inventory.py."""
from __future__ import annotations

import json
from pathlib import Path

from tools.generate_ssot_inventory import (
    ROOT,
    build_inventory,
    infer_target_ssot_key,
    scan_source_ast,
)


def test_scan_source_ast_extracts_constants_defaults_step_tables_and_patterns() -> None:
    """Verify AST visitor extracts module constants, dataclass fields, defaults, step-tables, and patterns."""
    sample_code = '''
from dataclasses import dataclass

DEFAULT_MIN_WORDS_NARRATIVE = 800
WORD_SALAD_PATTERNS = ("aspirational operating concept", "wedge")

@dataclass(frozen=True)
class Output2Config:
    min_word_count: int = 500
    max_word_count: int = 5000

def _get_tier1_word_limits(meeting_dur: int | None, has_diagrams: bool = False):
    if not meeting_dur or meeting_dur < 15:
        return 250, 1800, "short"
    return 400, 2400, "standard"

def execute_pass_2a(text: str, min_words: int = 800, max_retries: int = 3) -> str:
    return text
'''
    result = scan_source_ast(sample_code, "dual_pipeline/transcript_qa_core/gate2.py")

    const_symbols = {c["symbol"]: c["value"] for c in result["constants"]}
    assert const_symbols["DEFAULT_MIN_WORDS_NARRATIVE"] == 800
    assert const_symbols["Output2Config.min_word_count"] == 500
    assert const_symbols["Output2Config.max_word_count"] == 5000

    defaults = {(d["function"], d["parameter"]): d["default_value"] for d in result["default_bounds"]}
    assert defaults[("execute_pass_2a", "min_words")] == 800
    assert defaults[("execute_pass_2a", "max_retries")] == 3

    step_funcs = {s["function"]: s["numeric_literals"] for s in result["step_tables"]}
    assert "_get_tier1_word_limits" in step_funcs
    assert 250 in step_funcs["_get_tier1_word_limits"]
    assert 2400 in step_funcs["_get_tier1_word_limits"]

    patterns = [p["symbol"] for p in result["pattern_defs"]]
    assert "WORD_SALAD_PATTERNS" in patterns


def test_infer_target_ssot_key_mappings() -> None:
    """Verify deterministic mapping from file path and symbol to canonical SSOT keys."""
    assert (
        infer_target_ssot_key("dual_pipeline/llm_judge_core/recap_checks.py", "WORD_SALAD_PATTERNS")
        == "dual_pipeline.prompts.contracts.output2_contract.WORD_SALAD_PATTERNS"
    )
    assert (
        infer_target_ssot_key("dual_pipeline/transcript_qa_core/appendix_standard.py", "_C21_FLOORS")
        == "output2.c21_speaker_tier_floors"
    )
    assert (
        infer_target_ssot_key("dual_pipeline/live_meeting_recorder_core/copilot_engine.py", "max_words")
        == "copilot.speech.max_words"
    )
    assert (
        infer_target_ssot_key("dual_pipeline/audio_ingestion_core/whisperx_aligner.py", "max_words")
        == "audio_ingestion.max_words_per_utterance"
    )
    assert (
        infer_target_ssot_key("dual_pipeline/grounding/delivery_boundary.py", "max_words")
        == "grounding.delivery_max_words"
    )
    assert (
        infer_target_ssot_key("dual_pipeline/transcript_qa_core/gate2.py", "_get_tier1_word_limits")
        == "output2.word_limits.get_tier1_word_limits"
    )


def test_generated_ssot_inventory_captures_repo_drift_targets() -> None:
    """Verify reports/ssot_inventory.json exists and captures all target evaluator drift clusters."""
    inv_path = ROOT / "reports" / "ssot_inventory.json"
    assert inv_path.is_file(), "reports/ssot_inventory.json must exist"
    data = json.loads(inv_path.read_text(encoding="utf-8"))

    assert data["schema_version"] == "2.0.0"
    summary = data["summary"]
    assert summary["hardcoded_config_constants_count"] > 50
    assert summary["default_parameter_bounds_count"] > 20
    assert summary["step_table_functions_count"] >= 5
    assert summary["duplicate_pattern_clusters_count"] >= 5

    step_table_files = {item["file"] for item in data["step_table_functions"]}
    assert "dual_pipeline/transcript_qa_core/gate2.py" in step_table_files
    assert "tools/lint_constraint_bounds.py" in step_table_files

    pattern_Map = {item["symbol"]: item["occurrence_count"] for item in data["duplicate_pattern_clusters"]}
    assert pattern_Map.get("WORD_SALAD_PATTERNS", 0) >= 2
    assert pattern_Map.get("PASSIVE_HEDGING_PATTERNS", 0) >= 1
