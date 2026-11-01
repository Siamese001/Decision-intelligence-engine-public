"""End-to-end and adversarial verification for Wave 5: Prompt SSOT Deduplication & Shared Partial Extraction."""
from __future__ import annotations

import re
from pathlib import Path

from dual_pipeline.prompt_cache import PromptPrefixQuarantine, derive_cache_key
from dual_pipeline.prompts.compiler import CANONICAL_PROMPT_COMPILER
from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY, PROMPT_FILE_MAP
from tools.lint_prompt_ssot import (
    EXPECTED_CANONICAL_PROMPTS,
    REQUIRED_PLAN_STANDARD_TOKENS,
    SHARED_PARTIAL_NAME,
    load_assembled_automation_prompt,
    run_prompt_ssot_lint,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_duplicate_governance_prompts_removed_and_registry_valid() -> None:
    """Duplicate prompt copies in governance/decision_intelligence/ must not exist."""
    for dup_name in (
        "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md",
        "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md",
        "REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md",
    ):
        assert not (REPO_ROOT / "governance" / "decision_intelligence" / dup_name).exists()

    assert CANONICAL_PROMPT_REGISTRY.verify_manifest() is True
    for key in ("RAW_TRANSCRIPT_SYNTHESIS", "RAW_TRANSCRIPT_SYNTHESIS_V5_7", "REPO_TESTING_GAPS_REVIEW", "LIVE_COPILOT"):
        resolved_path = PROMPT_FILE_MAP[key]
        assert resolved_path.is_file()
        assert "config/prompts" in resolved_path.as_posix()


def test_stage_03_graph_extractor_has_zero_corpus_person_or_project_literals() -> None:
    """03_graph_extractor.md must contain zero hardcoded person or project literals."""
    stage_03_text = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_GRAPH")
    forbidden_literals = (
        "Jen Pan",
        "Project LENS",
        "Project Beacon",
        "system:rejected-image-bucket",
        "person:amit-ayer",
        "person:jake-stark",
        "person:miles-erickson",
        "person:dan-okray",
        "person:traci-metzger",
        "person:reid-schenk",
        "proposal:lens-relax-confidence-threshold",
    )
    for literal in forbidden_literals:
        assert not re.search(re.escape(literal), stage_03_text, re.IGNORECASE), (
            f"Forbidden corpus literal '{literal}' found in 03_graph_extractor.md"
        )


def test_shared_automation_governance_partial_and_ssot_linter() -> None:
    """All 16 automation prompts assemble with _shared_governance_contract.md and pass lint_prompt_ssot."""
    prompts_dir = REPO_ROOT / "automations" / "prompts"
    shared_path = prompts_dir / SHARED_PARTIAL_NAME
    assert shared_path.is_file()
    shared_text = shared_path.read_text(encoding="utf-8")
    for tok in REQUIRED_PLAN_STANDARD_TOKENS:
        assert tok in shared_text

    for fname in EXPECTED_CANONICAL_PROMPTS.values():
        assembled = load_assembled_automation_prompt(prompts_dir, fname)
        for tok in REQUIRED_PLAN_STANDARD_TOKENS:
            assert tok in assembled

    ok, errors = run_prompt_ssot_lint(REPO_ROOT)
    assert ok is True, f"Prompt SSOT linter errors: {errors}"


def test_unified_prompt_cache_and_compiler_manifest() -> None:
    """PromptPrefixQuarantine lives in dual_pipeline.prompt_cache and compiler manifest resolves cleanly."""
    assert not (REPO_ROOT / "dual_pipeline" / "token_optimization" / "prompt_cache.py").exists()
    quarantine = PromptPrefixQuarantine()
    sanitized, dynamic = quarantine.sanitize_prefix("Static rule\nCurrent Date/Time: 2026-10-28")
    assert "Current Date/Time" not in sanitized
    assert len(dynamic) == 1
    key = derive_cache_key(workflow="stage_03", model="gpt-5", static_prefix=sanitized)
    assert key.startswith("nee:stage_03:")

    manifest = CANONICAL_PROMPT_COMPILER.build_canonical_manifest()
    assert len(manifest) == 3
    for path, content in manifest.items():
        assert path.is_file()
        assert len(content) > 100
